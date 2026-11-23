"""Leased PostgreSQL sync jobs with permission rechecks and bounded retries."""
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.audit import audit
from app.connector_ingestion import mark_error, stage
from app.core.database import get_engine
from app.core.tenant import set_org
from app.jobs.celery_app import celery_app
from app.pg_connector import ConnectorError, Selection, extract, profile
from celery.exceptions import SoftTimeLimitExceeded

MAX_ATTEMPTS = 3


@celery_app.task(bind=True, ignore_result=True, acks_late=True, reject_on_worker_lost=True,
                 max_retries=MAX_ATTEMPTS, soft_time_limit=65, time_limit=75)
def sync_postgres(self: Any, org: str, job: str) -> None:
    tenant, sync = UUID(org), UUID(job)
    args = {"org": tenant, "id": sync, "lease": uuid4(), "attempts": MAX_ATTEMPTS}
    try:
        with get_engine().begin() as db:
            set_org(db, tenant)
            item = db.execute(text("""
              UPDATE app.pg_syncs SET status='running',attempts=attempts+1,lease_token=:lease,
                lease_until=clock_timestamp()+interval '90 seconds',error_code=NULL
              WHERE org_id=:org AND id=:id AND attempts<:attempts
                AND (status='queued' OR (status='running' AND lease_until<=clock_timestamp()))
              RETURNING actor_id,connector_id,config,attempts,created_at
            """), args).mappings().one_or_none()
            if item is None:
                db.execute(text("""
                  UPDATE app.pg_syncs SET status='failed',error_code='lease_exhausted',finished_at=clock_timestamp(),
                    lease_token=NULL,lease_until=NULL WHERE org_id=:org AND id=:id AND attempts>=:attempts
                    AND (status='queued' OR (status='running' AND lease_until<=clock_timestamp()))
                """), args)
                return
            identity = args | {"actor": item["actor_id"], "connector": item["connector_id"]}
            key = current_profile(db, identity)
    except OperationalError:
        raise self.retry(countdown=8) from None
    code = None
    retry = False
    try:
        if not key:
            raise ConnectorError("connector_access_revoked")
        selection = Selection.model_validate(item["config"])
        _, _, data = extract(profile(tenant, key), selection)
        with get_engine().begin() as db:
            set_org(db, tenant)
            db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": org})
            lease = db.execute(text("""
              SELECT id FROM app.pg_syncs WHERE org_id=:org AND id=:id AND lease_token=:lease FOR UPDATE
            """), args).scalar_one_or_none()
            if not lease:
                return
            if current_profile(db, identity) != key:
                raise ConnectorError("connector_access_revoked")
            count, version, recon = stage(db, tenant, item["connector_id"], item["actor_id"], selection, data,
                                          item["created_at"])
            db.execute(text("""
              UPDATE app.pg_syncs SET status='completed',row_count=:count,version_id=:version,
                recon_source_id=:recon,lease_token=NULL,lease_until=NULL,finished_at=clock_timestamp()
              WHERE org_id=:org AND id=:id AND lease_token=:lease
            """), args | {"count": count, "version": version, "recon": recon})
            db.execute(text("""
              UPDATE app.pg_connectors SET last_sync_at=clock_timestamp() WHERE org_id=:org AND id=:connector
            """), identity)
            audit(db, "connector.sync_completed", org=tenant, actor=item["actor_id"], resource=sync, correlation=sync)
    except ConnectorError as exc:
        code = exc.code
        retry = code in {"connector_busy", "source_connection_failed", "query_timeout"}
    except (OperationalError, SoftTimeLimitExceeded):
        code, retry = "temporary_failure", True
    except Exception:
        code = "sync_failed"
    if code:
        with get_engine().begin() as db:
            set_org(db, tenant)
            status = mark_error(db, args, code, retry and item["attempts"] < MAX_ATTEMPTS)
            if status:
                audit(db, "connector.sync_" + status, org=tenant, actor=item["actor_id"], resource=sync, correlation=sync)
        if status == "queued":
            raise self.retry(countdown=2 ** item["attempts"]) from None


def current_profile(db: Any, params: dict[str, Any]) -> str | None:
    return db.execute(text("""
      SELECT c.profile FROM app.pg_connectors c JOIN app.memberships m ON m.org_id=c.org_id
      WHERE c.org_id=:org AND c.id=:connector AND c.enabled AND NOT c.removed
        AND m.user_id=:actor AND m.role='admin'
    """), params).scalar_one_or_none()
