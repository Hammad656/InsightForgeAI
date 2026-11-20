"""Request, inspect and securely download private executive reports."""
import hashlib
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from kombu.exceptions import OperationalError
from pydantic import BaseModel, Field
from sqlalchemy import RowMapping, select, text
from sqlalchemy.dialects.postgresql import insert

from app.api.v1.analytics import Filters, resolve_period
from app.api.v1.auth import Db, User, digest, limit_key
from app.api.v1.organizations import Role, locked_org, tenant, write
from app.audit import audit, correlation_id
from app.data_quality import quality
from app.jobs.reports import generate_report
from app.report_models import Report, ReportDownload
from app.report_storage import get_storage

router = APIRouter(prefix="/reports")


class ReportRequest(Filters):
    organization_id: UUID
    request_key: UUID
    template: Literal["monthly-performance"] = "monthly-performance"
    start: date
    end: date
    query_id: UUID | None = None


class DownloadBody(BaseModel):
    token: str = Field(min_length=43, max_length=43)


def report_access(db: Db, org: UUID, user: UUID, report: UUID, role: Role) -> RowMapping:
    row = db.execute(select(Report).where(
        Report.org_id == org, Report.id == report,
    )).mappings().one_or_none()
    if row is None or (role != "admin" and row["created_by"] != user):
        raise HTTPException(404, "Report not found")
    return row


def metadata(row: RowMapping) -> dict[str, Any]:
    return {key: row[key] for key in (
        "id", "template", "status", "filters", "sources", "created_at", "finished_at",
        "expires_at", "artifact_size", "artifact_sha256", "error_code", "query_id",
    )} | {"retry_available": row["status"] in {"queued", "failed"} or (
        row["status"] == "processing" and row["lease_until"] <= datetime.now(timezone.utc)
    )}


def enqueue(org: UUID, report: UUID) -> None:
    try:
        generate_report.apply_async(args=(str(org), str(report)), retry=False)
    except OperationalError:
        # The committed report remains queued and can be dispatched from history.
        return


@router.post("", status_code=202, dependencies=write)
def submit(body: ReportRequest, db: Db, user: User) -> dict[str, Any]:
    org = body.organization_id
    locked_org(db, org)
    role = tenant(org, db, user)
    if role == "viewer":
        raise HTTPException(403, "Analyst or administrator permission required")
    limit_key(f"if:reports:{user}", 12)
    if body.start.day != 1 or body.end.day != 1 or not 28 <= (body.end - body.start).days <= 31:
        raise HTTPException(422, "Select one complete calendar month")
    if any(value and "\x00" in value for value in (body.branch, body.sku, body.customer)):
        raise HTTPException(422, "Invalid dimension filter")
    today = db.execute(text("SELECT (now() AT TIME ZONE 'Asia/Karachi')::date")).scalar_one()
    params = resolve_period(Filters.model_validate(body.model_dump()), today)
    filters = jsonable_encoder(params, custom_encoder={Decimal: str})
    filters["requested_as_of"] = body.as_of.isoformat() if body.as_of else None
    sources = {row["domain"]: jsonable_encoder({
        "version": row["version"], "published_at": row["published_at"].astimezone(timezone.utc),
    })
               for row in db.execute(text("""
        SELECT d.domain,v.id AS version,v.published_at
        FROM app.datasets d JOIN biz.dataset_versions v ON (v.org_id,v.domain,v.id)=(d.org_id,d.domain,d.active_version)
        WHERE d.org_id=:org
    """), {"org": org}).mappings()}
    existing = db.execute(select(Report).where(
        Report.org_id == org, Report.created_by == user, Report.request_key == body.request_key,
    )).mappings().one_or_none()
    if existing is None and body.query_id is not None:
        reference = db.execute(text("""
            SELECT result->'evidence'->'dataset_versions' AS versions FROM app.ai_queries
            WHERE org_id=:org AND author_id=:actor AND id=:id AND result->>'status'='completed'
        """), {"org": org, "actor": user, "id": body.query_id}).scalar_one_or_none()
        if not reference or not set(reference).issubset({source["version"] for source in sources.values()}):
            raise HTTPException(404, "Matching query evidence not found")
    if existing is None:
        assessment = quality(db, org)
        if assessment and assessment["state"] == "blocked":
            raise HTTPException(409, assessment["message"])
        if assessment:
            filters["data_quality"] = jsonable_encoder(assessment)
        inserted = db.execute(insert(Report).values(
            id=uuid4(), org_id=org, created_by=user, request_key=body.request_key, template=body.template,
            filters=filters, sources=sources, query_id=body.query_id, correlation_id=correlation_id(),
        ).on_conflict_do_nothing(index_elements=[Report.org_id, Report.created_by, Report.request_key])
            .returning(Report.id)).scalar_one_or_none()
        existing = db.execute(select(Report).where(
            Report.org_id == org, Report.created_by == user, Report.request_key == body.request_key,
        )).mappings().one()
        if inserted:
            audit(db, "report.requested", org=org, resource=inserted)
    if any(existing["filters"].get(key) != value for key, value in filters.items() if key not in {"as_of", "data_quality"}):
        raise HTTPException(409, "This request key already belongs to a different report")
    if existing["query_id"] != body.query_id:
        raise HTTPException(409, "This request key already belongs to a different report")
    result = metadata(existing)
    db.commit()
    if result["status"] == "queued":
        enqueue(org, result["id"])
    return result


@router.get("")
def history(
    organization_id: UUID, db: Db, user: User, limit: int = Query(50, ge=1, le=100),
) -> list[dict[str, Any]]:
    role = tenant(organization_id, db, user)
    statement = select(Report).where(Report.org_id == organization_id)
    if role != "admin":
        statement = statement.where(Report.created_by == user)
    return [metadata(row) for row in db.execute(
        statement.order_by(Report.created_at.desc(), Report.id.desc()).limit(limit)
    ).mappings()]


@router.get("/{report_id}")
def status(report_id: UUID, organization_id: UUID, db: Db, user: User) -> dict[str, Any]:
    return metadata(report_access(db, organization_id, user, report_id, tenant(organization_id, db, user)))


@router.post("/{report_id}/retry", status_code=202, dependencies=write)
def retry(report_id: UUID, organization_id: UUID, db: Db, user: User) -> dict[str, Any]:
    org = organization_id
    role = tenant(org, db, user)
    if role == "viewer":
        raise HTTPException(403, "Analyst or administrator permission required")
    limit_key(f"if:reports:{user}", 12)
    row = report_access(db, org, user, report_id, role)
    updated = db.execute(text("""
        UPDATE app.reports SET status='queued',run_token=NULL,lease_until=NULL,attempts=0,finished_at=NULL,error_code=NULL
        WHERE org_id=:org AND id=:id AND (status='failed' OR (status='processing' AND lease_until<=clock_timestamp()))
    """), {"org": org, "id": report_id})
    if updated.rowcount:
        audit(db, "report.retried", org=org, resource=report_id)
    if row["status"] == "queued" or updated.rowcount:
        result = status(report_id, org, db, user)
        db.commit()
        enqueue(org, report_id)
        return result
    return metadata(row)


def download_access(db: Db, org: UUID, user: UUID, report: UUID) -> RowMapping:
    locked_org(db, org)
    row = report_access(db, org, user, report, tenant(org, db, user))
    if row["status"] != "completed":
        raise HTTPException(409, "Report is not ready")
    if row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(410, "Report retention period has ended")
    return row


@router.post("/{report_id}/download-grant", dependencies=write)
def grant(report_id: UUID, organization_id: UUID, db: Db, user: User) -> dict[str, Any]:
    org = organization_id
    row = download_access(db, org, user, report_id)
    limit_key(f"if:report-grants:{user}", 20)
    token = secrets.token_urlsafe(32)
    db.execute(text("""
        DELETE FROM app.report_downloads WHERE org_id=:org AND actor_id=:actor AND expires_at<=clock_timestamp()
    """), {"org": org, "actor": user})
    expires = db.execute(insert(ReportDownload).values(
        org_id=org, report_id=report_id, actor_id=user, token_hash=digest(token),
        expires_at=db.execute(text("SELECT least(clock_timestamp()+interval '5 minutes',:expires)"),
                              {"expires": row["expires_at"]}).scalar_one(),
    ).returning(ReportDownload.expires_at)).scalar_one()
    audit(db, "report.download_authorized", org=org, resource=report_id)
    return {"token": token, "expires_at": expires}


@router.post("/{report_id}/download", dependencies=write)
def download(report_id: UUID, organization_id: UUID, body: DownloadBody, db: Db, user: User) -> Response:
    org = organization_id
    row = download_access(db, org, user, report_id)
    used = db.execute(text("""
        DELETE FROM app.report_downloads WHERE org_id=:org AND report_id=:id AND actor_id=:actor
        AND token_hash=:hash AND expires_at>clock_timestamp() RETURNING report_id
    """), {"org": org, "id": report_id, "actor": user, "hash": digest(body.token)}).scalar_one_or_none()
    if used is None:
        raise HTTPException(404, "Download authorization is invalid or expired")
    try:
        content = get_storage().get(org, report_id)
    except OSError:
        raise HTTPException(503, "Report artifact is temporarily unavailable") from None
    if len(content) != row["artifact_size"] or hashlib.sha256(content).hexdigest() != row["artifact_sha256"]:
        raise HTTPException(503, "Report artifact could not be verified")
    audit(db, "report.downloaded", org=org, resource=report_id)
    return Response(content, media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="monthly-business-performance.pdf"',
        "X-Content-Type-Options": "nosniff",
    })
