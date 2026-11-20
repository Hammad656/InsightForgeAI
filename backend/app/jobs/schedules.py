"""Durable schedule dispatch with tenant-local run and report records."""
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from kombu.exceptions import OperationalError as BrokerUnavailable
from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError

from app.api.v1.analytics import Filters, resolve_period
from app.audit import audit
from app.core.database import get_engine
from app.core.tenant import set_org
from app.data_quality import quality
from app.jobs.celery_app import celery_app
from app.jobs.reports import ATTEMPTS, generate_report
from app.report_schedules import ScheduleConfig, next_occurrence, report_period


def wakeup(db: Connection, org: UUID, schedule: UUID, next_at: datetime) -> None:
    db.execute(text("""
      INSERT INTO app.schedule_wakeups VALUES (:org,:id,:due)
      ON CONFLICT(org_id,schedule_id) DO UPDATE SET next_at=excluded.next_at
    """), {"org": org, "id": schedule, "due": next_at})


def prepare(org: UUID, schedule: UUID, now: datetime) -> list[UUID]:
    with get_engine().begin() as db:
        set_org(db, org)
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": str(org)})
        args = {"org": org, "id": schedule, "now": now, "attempts": ATTEMPTS}
        row = db.execute(text("""
          SELECT s.*,m.role FROM app.report_schedules s LEFT JOIN app.memberships m
          ON (s.org_id,s.owner_id)=(m.org_id,m.user_id) WHERE s.org_id=:org AND s.id=:id
        """), args).mappings().one_or_none()
        if row is None:
            return []
        due = row["next_at"]
        if due <= now and not row["paused"]:
            config = ScheduleConfig.model_validate(row["config"])
            trace = uuid4()
            report = None
            error = None
            assessment = quality(db, org)
            if row["role"] not in {"admin", "analyst"}:
                error = "permission_revoked"
            elif assessment and assessment["state"] == "blocked":
                error = "data_quality_blocked"
            else:
                start, end = report_period(config, due)
                filters = resolve_period(Filters(start=start, end=end, as_of=end - timedelta(days=1)), end)
                filters["report_title"] = "Scheduled Business Performance"
                filters["business_timezone"] = config.timezone
                if assessment:
                    filters["data_quality"] = assessment
                sources = {source["domain"]: {"version": source["version"], "published_at": source["published_at"]}
                           for source in db.execute(text("""
                    SELECT d.domain,v.id AS version,v.published_at
                    FROM app.datasets d JOIN biz.dataset_versions v
                      ON (v.org_id,v.domain,v.id)=(d.org_id,d.domain,d.active_version)
                    WHERE d.org_id=:org
                """), args).mappings()}
                report = uuid4()
                db.execute(text("""
                  INSERT INTO app.reports(org_id,id,created_by,request_key,template,filters,sources,correlation_id)
                  VALUES (:org,:report,:actor,:report,'monthly-performance',CAST(:filters AS jsonb),
                          CAST(:sources AS jsonb),:trace)
                """), args | {"report": report, "actor": row["owner_id"], "trace": trace,
                             "filters": json.dumps(jsonable_encoder(filters)),
                             "sources": json.dumps(jsonable_encoder(sources))})
            db.execute(text("""
              INSERT INTO app.report_schedule_runs VALUES (:org,:id,:due,:report,:error)
            """), args | {"due": due, "report": report, "error": error})
            audit(db, "schedule.failed" if error else "schedule.dispatched", org=org,
                  actor=row["owner_id"], resource=schedule, correlation=trace)
            next_at = next_occurrence(config, now)
            db.execute(text("UPDATE app.report_schedules SET next_at=:due WHERE org_id=:org AND id=:id"),
                       args | {"due": next_at})
        else:
            next_at = due

        db.execute(text("""
          UPDATE app.reports r SET status='failed',error_code='worker_lease_exhausted',
                 finished_at=clock_timestamp(),run_token=NULL,lease_until=NULL
          FROM app.report_schedule_runs run
          WHERE (r.org_id,r.id)=(run.org_id,run.report_id) AND run.org_id=:org AND run.schedule_id=:id
            AND r.status='processing' AND r.lease_until<=:now AND r.attempts>=:attempts
        """), args)
        pending = db.execute(text("""
          SELECT r.id,r.status,r.lease_until FROM app.report_schedule_runs run JOIN app.reports r
            ON (r.org_id,r.id)=(run.org_id,run.report_id)
          WHERE run.org_id=:org AND run.schedule_id=:id AND r.status IN ('queued','processing')
          ORDER BY run.due_at LIMIT 10
        """), args).mappings().all()
        recovery = now + timedelta(minutes=2)
        if row["paused"] and not pending:
            db.execute(text("DELETE FROM app.schedule_wakeups WHERE org_id=:org AND schedule_id=:id"), args)
        else:
            wakeup(db, org, schedule, recovery if row["paused"] else min(next_at, recovery) if pending else next_at)
        return [item["id"] for item in pending if item["status"] == "queued" or item["lease_until"] <= now]


@celery_app.task(ignore_result=True, autoretry_for=(OperationalError, BrokerUnavailable), retry_backoff=True,
                 max_retries=3, soft_time_limit=45, time_limit=55)
def dispatch_schedules() -> None:
    now = datetime.now(timezone.utc)
    with get_engine().connect() as db:
        due = db.execute(text("SELECT org_id,schedule_id FROM app.due_report_schedules()")).all()
    for org, schedule in due:
        for report in prepare(org, schedule, now):
            generate_report.apply_async(args=(str(org), str(report)), retry=False)
