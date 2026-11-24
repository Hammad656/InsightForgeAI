"""Run bounded forecast training under a tenant lease."""
from typing import Any
from uuid import UUID, uuid4

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError

from app.audit import audit
from app.core.database import get_engine
from app.core.tenant import set_org
from app.data_quality import quality
from app.forecast_data import calculate
from app.jobs.analyses import encoded
from app.jobs.celery_app import celery_app
from app.predictive import ForecastSpec, monday

MAX_ATTEMPTS = 3


def eligible(db: Connection, org: UUID, author: UUID) -> bool:
    return db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM app.memberships WHERE org_id=:org AND user_id=:author
          AND role IN ('admin','analyst'))
    """), {"org": org, "author": author}).scalar_one() is True


@celery_app.task(bind=True, ignore_result=True, acks_late=True, reject_on_worker_lost=True,
                 max_retries=MAX_ATTEMPTS, soft_time_limit=40, time_limit=50)
def run_forecast(self: Any, organization_id: str, run_id: str) -> None:
    org, run = UUID(organization_id), UUID(run_id)
    args = {"org": org, "id": run, "lease": uuid4(), "max_attempts": MAX_ATTEMPTS}
    try:
        with get_engine().begin() as db:
            set_org(db, org)
            job = db.execute(text("""
                UPDATE app.forecast_runs SET status='running', attempts=attempts+1, lease_token=:lease,
                  lease_until=clock_timestamp()+interval '60 seconds',error_code=NULL
                WHERE org_id=:org AND id=:id AND attempts<:max_attempts
                  AND (status='queued' OR status='running' AND lease_until<=clock_timestamp())
                RETURNING author_id,config,sources,attempts
            """), args).mappings().one_or_none()
            if job is None:
                db.execute(text("""
                    UPDATE app.forecast_runs SET status='failed',error_code='lease_exhausted',
                      finished_at=clock_timestamp(),lease_token=NULL,lease_until=NULL
                    WHERE org_id=:org AND id=:id AND attempts>=:max_attempts
                      AND (status='queued' OR status='running' AND lease_until<=clock_timestamp())
                """), args)
                return
            audit(db, "forecast.started", org=org, actor=job["author_id"], resource=run, correlation=run)
    except OperationalError:
        raise self.retry(countdown=5) from None

    result = artifact = assessment = error = None
    code = "failed"
    retry = False
    try:
        spec = ForecastSpec.model_validate(job["config"])
        if spec.as_of is None:
            raise ValueError("Missing fixed training cutoff")
        with get_engine().begin() as db:
            set_org(db, org)
            db.execute(text("SET LOCAL statement_timeout='3500ms'"))
            assessment = quality(db, org)
            if not eligible(db, org, job["author_id"]):
                error = "permission_revoked"
            elif assessment and assessment["state"] == "blocked":
                code, error = "blocked", "reconciliation_blocked"
            else:
                result, artifact = calculate(db, org, spec, monday(spec.as_of), job["sources"])
                code = result["status"]
                if assessment is None:
                    result["limits"].append("No reconciliation assessment is recorded for this organization.")
    except (OperationalError, SoftTimeLimitExceeded):
        retry = job["attempts"] < MAX_ATTEMPTS
        code, error = ("queued" if retry else "failed"), "temporary_failure"
    except Exception:
        code, error = "failed", "training_failed"

    try:
        with get_engine().begin() as db:
            set_org(db, org)
            db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": organization_id})
            current = quality(db, org)
            if not eligible(db, org, job["author_id"]):
                code, error, result, artifact, retry = "failed", "permission_revoked", None, None, False
            elif current and current["state"] == "blocked":
                code, error, result, artifact, retry = "blocked", "reconciliation_blocked", None, None, False
            saved = db.execute(text("""
                UPDATE app.forecast_runs SET status=:status,error_code=:error,lease_token=NULL,lease_until=NULL,
                  result=CAST(:result AS jsonb),artifact=CAST(:artifact AS jsonb),evidence=CAST(:evidence AS jsonb),
                  artifact_expires_at=CASE WHEN :status='completed' THEN clock_timestamp()+interval '30 days' END,
                  finished_at=CASE WHEN :status='queued' THEN NULL ELSE clock_timestamp() END
                WHERE org_id=:org AND id=:id AND lease_token=:lease RETURNING id
            """), args | {"status": code, "error": error, "result": encoded(result), "artifact": encoded(artifact),
                         "evidence": encoded({"training_quality": assessment, "publication_quality": current})}).scalar_one_or_none()
            if saved:
                audit(db, "forecast." + code, org=org, actor=job["author_id"], resource=run, correlation=run)
            db.execute(text("""
                UPDATE app.forecast_runs SET artifact=NULL
                WHERE org_id=:org AND artifact IS NOT NULL AND artifact_expires_at<=clock_timestamp()
            """), args)
    except OperationalError:
        raise self.retry(countdown=62) from None
    if retry:
        raise self.retry(countdown=5) from None
