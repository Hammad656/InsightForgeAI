"""Idempotent tenant-scoped evaluation run jobs."""
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.audit import audit
from app.core.database import get_engine
from app.core.tenant import set_org
from app.evaluation import execute
from app.jobs.celery_app import celery_app

RETRIES = 3
AUTHORIZED = """EXISTS (SELECT 1 FROM app.memberships m
    WHERE (m.org_id,m.user_id)=(r.org_id,r.actor_id)
      AND (m.role='admin' OR (m.role='analyst' AND r.manifest->>'mode'<>'live_provider')))"""


@celery_app.task(bind=True, ignore_result=True, acks_late=True, reject_on_worker_lost=True,
                 soft_time_limit=210, time_limit=240, max_retries=RETRIES)
def evaluate_run(self: Any, org_id: str, run_id: str) -> None:
    org, run, lease = UUID(org_id), UUID(run_id), uuid4()
    args = {"org": org, "id": run, "lease": lease}
    try:
        with get_engine().begin() as db:
            set_org(db, org)
            job = db.execute(text(f"""
                UPDATE app.evaluation_runs r SET status='running',attempts=attempts+1,lease_token=:lease,
                    lease_until=clock_timestamp()+interval '4 minutes'
                WHERE org_id=:org AND id=:id AND attempts<3 AND {AUTHORIZED}
                  AND (status='queued' OR (status='running' AND lease_until<=clock_timestamp()))
                  AND (manifest->>'mode'<>'live_provider' OR attempts=0)
                RETURNING actor_id,model,manifest->>'mode' AS mode
            """), args).mappings().one_or_none()
            if job is None:
                db.execute(text(f"""
                    UPDATE app.evaluation_runs r SET status='failed',error_code=CASE WHEN {AUTHORIZED}
                      THEN 'lease_exhausted' ELSE 'permission_revoked' END,finished_at=clock_timestamp()
                    WHERE org_id=:org AND id=:id
                      AND (status='queued' OR (status='running' AND lease_until<=clock_timestamp()))
                      AND (attempts>=3 OR NOT {AUTHORIZED} OR (manifest->>'mode'='live_provider' AND attempts>0))
                """), args)
                return
            actor = job["actor_id"]
            audit(db, "evaluation.started", org=org, actor=actor, resource=run, correlation=run)
    except OperationalError:
        raise self.retry(countdown=10) from None
    try:
        results, scores = execute(job["mode"], job["model"])
    except Exception as exc:
        transient = job["mode"] != "live_provider" and isinstance(exc, OperationalError)
        with get_engine().begin() as db:
            set_org(db, org)
            retry = db.execute(text("""
                UPDATE app.evaluation_runs SET status=CASE WHEN :transient AND attempts<3 THEN 'queued'
                    ELSE 'failed' END, lease_token=NULL, lease_until=NULL, error_code='execution_failed',
                    finished_at=CASE WHEN :transient AND attempts<3 THEN NULL ELSE clock_timestamp() END
                WHERE org_id=:org AND id=:id AND lease_token=:lease RETURNING status
            """), args | {"transient": transient}).scalar_one_or_none()
            audit(db, "evaluation.retry" if retry == "queued" else "evaluation.failed",
                  org=org, actor=actor, resource=run, correlation=run)
        if retry == "queued":
            raise self.retry(countdown=10) from None
        return
    with get_engine().begin() as db:
        set_org(db, org)
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": str(org)})
        status = db.execute(text(f"""
            UPDATE app.evaluation_runs r SET status=CASE WHEN {AUTHORIZED} THEN 'completed' ELSE 'failed' END,
                error_code=CASE WHEN {AUTHORIZED} THEN NULL ELSE 'permission_revoked' END,
                results=CASE WHEN {AUTHORIZED} THEN CAST(:results AS jsonb) ELSE NULL END,
                scores=CASE WHEN {AUTHORIZED} THEN CAST(:scores AS jsonb) ELSE NULL END,
                lease_token=NULL,lease_until=NULL,finished_at=clock_timestamp()
            WHERE org_id=:org AND id=:id AND lease_token=:lease RETURNING status
        """), args | {"results": json.dumps(results), "scores": json.dumps(scores)}).scalar_one_or_none()
        if status:
            audit(db, "evaluation." + status, org=org, actor=actor, resource=run, correlation=run)
