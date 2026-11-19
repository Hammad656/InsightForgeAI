"""Generate reports asynchronously with a bounded lease and atomic publication."""
import hashlib
from typing import Any
from uuid import UUID, uuid4

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.audit import audit
from app.core.database import get_engine
from app.core.tenant import set_org
from app.executive_report import collect, render
from app.jobs.celery_app import celery_app
from app.report_storage import MAX_ARTIFACT_BYTES, get_storage

ATTEMPTS = 3


def claim(org: UUID, report: UUID, token: UUID) -> dict[str, Any] | None:
    with get_engine().begin() as db:
        set_org(db, org)
        row = db.execute(text("""
            UPDATE app.reports SET status='processing', attempts=attempts+1, run_token=:token,
                lease_until=clock_timestamp()+interval '3 minutes', error_code=NULL
            WHERE org_id=:org AND id=:id AND attempts<:attempts
              AND (status='queued' OR (status='processing' AND lease_until<=clock_timestamp()))
            RETURNING created_by, filters, sources, query_id, correlation_id, attempts
        """), {"org": org, "id": report, "token": token, "attempts": ATTEMPTS}).mappings().one_or_none()
        return dict(row) if row else None


def fail(org: UUID, report: UUID, token: UUID, job: dict[str, Any], code: str, retry: bool) -> None:
    with get_engine().begin() as db:
        set_org(db, org)
        updated = db.execute(text("""
            UPDATE app.reports SET status=:status, error_code=:error, run_token=NULL, lease_until=NULL,
                finished_at=CASE WHEN :finished THEN clock_timestamp() ELSE NULL END
            WHERE org_id=:org AND id=:id AND run_token=:token
        """), {"org": org, "id": report, "token": token, "status": "queued" if retry else "failed",
               "finished": not retry, "error": code})
        if updated.rowcount and not retry:
            audit(db, "report.failed", org=org, actor=job["created_by"], resource=report,
                  correlation=job["correlation_id"])


class AccessRevoked(RuntimeError):
    pass


def generate(org: UUID, report: UUID, token: UUID, job: dict[str, Any]) -> None:
    with get_engine().begin() as db:
        set_org(db, org)
        organization = db.execute(text("""
            SELECT o.name FROM app.organizations o JOIN app.memberships m ON m.org_id=o.id
            WHERE o.id=:org AND m.user_id=:user AND m.role IN ('admin','analyst')
        """), {"org": org, "user": job["created_by"]}).scalar_one_or_none()
        if organization is None:
            raise AccessRevoked
        data = collect(db, org, job["filters"], job["sources"])
    content = render(report, organization, job["filters"], job["sources"], data, job["query_id"])
    if not 0 < len(content) <= MAX_ARTIFACT_BYTES:
        raise ValueError("Report artifact size is outside policy")
    with get_engine().begin() as db:
        set_org(db, org)
        # Membership changes use the same advisory lock in the organization API.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": str(org)})
        active = db.execute(text("""
            SELECT r.id FROM app.reports r JOIN app.memberships m ON (m.org_id,m.user_id)=(r.org_id,r.created_by)
            WHERE r.org_id=:org AND r.id=:id AND r.run_token=:token AND m.role IN ('admin','analyst')
            FOR UPDATE OF r
        """), {"org": org, "id": report, "token": token}).scalar_one_or_none()
        if active is None:
            raise AccessRevoked
        get_storage().put(org, report, content)
        db.execute(text("""
            UPDATE app.reports SET status='completed', artifact_sha256=:sha, artifact_size=:size,
                run_token=NULL, lease_until=NULL, finished_at=clock_timestamp(),
                expires_at=clock_timestamp()+interval '30 days'
            WHERE org_id=:org AND id=:id
        """), {"org": org, "id": report, "sha": hashlib.sha256(content).hexdigest(), "size": len(content)})
        audit(db, "report.completed", org=org, actor=job["created_by"], resource=report,
              correlation=job["correlation_id"])


def purge_expired(org: UUID) -> None:
    with get_engine().begin() as db:
        set_org(db, org)
        expired = db.execute(text("""
            SELECT id FROM app.reports WHERE org_id=:org AND status='completed'
            AND expires_at<=clock_timestamp() AND purged_at IS NULL ORDER BY expires_at
            LIMIT 5 FOR UPDATE SKIP LOCKED
        """), {"org": org}).scalars().all()
        for expired_id in expired:
            try:
                get_storage().delete(org, expired_id)
            except OSError:
                continue
            db.execute(text("""
                UPDATE app.reports SET purged_at=clock_timestamp() WHERE org_id=:org AND id=:id
            """), {"org": org, "id": expired_id})


@celery_app.task(bind=True, ignore_result=True, acks_late=True, reject_on_worker_lost=True,
                 max_retries=ATTEMPTS, soft_time_limit=75, time_limit=90)
def generate_report(self: Any, org_id: str, report_id: str) -> None:
    org, report, token = UUID(org_id), UUID(report_id), uuid4()
    try:
        job = claim(org, report, token)
    except OperationalError:
        raise self.retry(countdown=10) from None
    if job is None:
        return
    try:
        generate(org, report, token, job)
    except AccessRevoked:
        fail(org, report, token, job, "permission_revoked", False)
    except (OSError, OperationalError, SoftTimeLimitExceeded):
        retry = job["attempts"] < ATTEMPTS
        fail(org, report, token, job, "temporary_failure", retry)
        if retry:
            raise self.retry(countdown=2 ** job["attempts"]) from None
    except Exception:
        fail(org, report, token, job, "generation_failed", False)
    else:
        try:
            purge_expired(org)
        except (OperationalError, SoftTimeLimitExceeded):
            pass
