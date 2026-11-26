"""Run a reconciliation once, with source data and issue publication in one transaction."""
import json
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.audit import audit
from app.core.database import get_engine
from app.core.telemetry import emit
from app.core.tenant import set_org
from app.jobs.celery_app import celery_app
from app.reconciliation import Reconciler


@celery_app.task(acks_late=True, ignore_result=True, reject_on_worker_lost=True, soft_time_limit=75, time_limit=90,
                 autoretry_for=(OperationalError,), max_retries=4, retry_backoff=True)
def reconcile_sources(org_id: str, run_id: str) -> None:
    org, run = UUID(org_id), UUID(run_id)
    try:
        process(org, run)
    except OperationalError:
        raise
    except Exception:
        with get_engine().begin() as db:
            set_org(db, org)
            failed = db.execute(text("""
                UPDATE app.recon_runs SET status='failed',finished_at=now(),summary='{"error":"reconciliation_failed"}'
                WHERE org_id=:org AND id=:run AND status='queued' RETURNING actor_id,correlation_id
            """), {"org": org, "run": run}).one_or_none()
            if failed is not None:
                audit(db, "reconciliation.failed", org=org, actor=failed.actor_id, resource=run,
                      correlation=failed.correlation_id)


def process(org: UUID, run: UUID) -> None:
    params = {"org": org, "run": run}
    with get_engine().begin() as db:
        set_org(db, org)
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": str(org)})
        job = db.execute(text("""
            SELECT r.*,m.role FROM app.recon_runs r LEFT JOIN app.memberships m
              ON (m.org_id,m.user_id)=(r.org_id,r.actor_id)
            WHERE r.org_id=:org AND r.id=:run AND r.status='queued' FOR UPDATE OF r
        """), params).mappings().one_or_none()
        if job is None:
            return
        actor, correlation = job["actor_id"], job["correlation_id"]
        if job["role"] not in {"admin", "analyst"}:
            db.execute(text("""
                UPDATE app.recon_runs SET status='failed',finished_at=now(),summary='{"error":"permission_revoked"}'
                WHERE org_id=:org AND id=:run
            """), params)
            audit(db, "reconciliation.failed", org=org, actor=actor, resource=run, correlation=correlation)
            return
        aliases = {(row.source, row.kind, row.source_key): row.canonical_key for row in db.execute(text("""
            SELECT a.source,a.kind,a.source_key,e.canonical_key FROM app.recon_aliases a
            JOIN app.recon_entities e ON (e.org_id,e.kind,e.id)=(a.org_id,a.kind,a.entity_id)
            WHERE a.org_id=:org AND a.id=ANY(:aliases)
        """), {**params, "aliases": job["alias_ids"]})}
        sources = [dict(row) for row in db.execute(text("""
            SELECT s.id,s.source,s.domain,s.revision,s.header,s.records,s.options,
                (SELECT max(h.revision) FROM app.recon_sources h
                 WHERE (h.org_id,h.source,h.domain)=(s.org_id,s.source,s.domain)) AS latest_revision
            FROM app.recon_sources s WHERE s.org_id=:org AND s.id=ANY(:inputs)
            ORDER BY s.source,s.domain,s.revision DESC,s.id
        """), {**params, "inputs": job["input_ids"]}).mappings()]
        result = Reconciler(aliases, job["as_of"]).result(sources)
        issues = result.pop("issues")
        if issues:
            db.execute(text("""
                INSERT INTO app.recon_issues(org_id,id,run_id,rule,severity,refs,expected,observed)
                VALUES (:org,:id,:run,:rule,:severity,CAST(:refs AS jsonb),:expected,:observed)
            """), [{**params, **issue, "id": uuid4(), "refs": json.dumps(issue["refs"])} for issue in issues])
        result["issue_count"] = len(issues)
        db.execute(text("""
            UPDATE app.recon_runs SET status='completed',finished_at=now(),summary=CAST(:summary AS jsonb)
            WHERE org_id=:org AND id=:run
        """), {**params, "summary": json.dumps(result)})
        audit(db, "reconciliation.completed", org=org, actor=actor, resource=run, correlation=correlation)
    emit("reconciliation.summary", issue_count=len(issues))
