"""Idempotent asynchronous snapshot publication."""
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.database import get_engine
from app.core.tenant import set_org
from app.ingestion import FIELDS, InvalidFile, parse, validate
from app.jobs.celery_app import celery_app


@celery_app.task(
    ignore_result=True, acks_late=True, autoretry_for=(OperationalError,), retry_backoff=True,
    max_retries=5, soft_time_limit=90, time_limit=120, reject_on_worker_lost=True,
)
def import_snapshot(org: str, job_id: str) -> None:
    params = {"org": UUID(org), "id": UUID(job_id)}
    with get_engine().begin() as db:
        set_org(db, params["org"])
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": org})
        job = db.execute(text("""
            SELECT j.domain,j.mapping,j.created_at,u.filename,u.content,m.role
            FROM app.import_jobs j JOIN app.uploads u ON (u.org_id,u.id)=(j.org_id,j.upload_id)
            LEFT JOIN app.memberships m ON (m.org_id,m.user_id)=(j.org_id,j.requested_by)
            WHERE j.org_id=:org AND j.id=:id AND j.status='queued' FOR UPDATE OF j
        """), params).one_or_none()
        if job is None:
            return
        rows: list[dict[str, str]] = []
        errors: list[dict[str, int | str]]
        if job.role != "admin":
            errors = [{"row": 0, "message": "Import requester no longer has administrator permission"}]
        else:
            try:
                headers, data = parse(job.filename, job.content)
                rows, errors = validate(job.domain, job.mapping, headers, data)
            except InvalidFile as exc:
                errors = [{"row": 0, "message": str(exc)}]
        info = {**params, "domain": job.domain, "created": job.created_at}
        if errors:
            db.execute(text("""
                UPDATE app.import_jobs SET status='failed',finished_at=now(),row_count=0,errors=CAST(:errors AS jsonb)
                WHERE org_id=:org AND id=:id
            """), {**info, "errors": json.dumps(errors)})
            return
        db.execute(text("INSERT INTO biz.dataset_versions (org_id,id,domain) VALUES (:org,:id,:domain)"), info)
        source_key = FIELDS[job.domain][0]
        db.execute(text("""
            INSERT INTO biz.dataset_rows (org_id,version_id,source_key,payload)
            VALUES (:org,:id,:key,CAST(:payload AS jsonb))
        """), [{**info, "key": row[source_key], "payload": json.dumps(row)} for row in rows])
        db.execute(text("""
            UPDATE app.datasets d SET active_version=:id WHERE d.org_id=:org AND d.domain=:domain
            AND (d.active_version IS NULL OR EXISTS (
                SELECT 1 FROM app.import_jobs j WHERE j.org_id=d.org_id AND j.id=d.active_version
                AND (j.created_at,j.id)<(:created,:id)
            ))
        """), info)
        db.execute(text("""
            UPDATE app.import_jobs SET status='published',finished_at=now(),row_count=:count
            WHERE org_id=:org AND id=:id
        """), {**info, "count": len(rows)})
