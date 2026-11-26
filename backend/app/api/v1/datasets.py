"""Tenant-authorized upload, preview and import APIs."""
import hashlib
import json
import re
from datetime import date
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, UploadFile
from kombu.exceptions import OperationalError
from pydantic import BaseModel, Field
from sqlalchemy import Row, text

from app.api.v1.auth import Db, User
from app.api.v1.organizations import Admin, Tenant, write
from app.audit import audit
from app.ingestion import FIELDS, MAX_BYTES, Domain, InvalidFile, parse, suggest_mapping
from app.jobs.imports import import_snapshot

router = APIRouter(prefix="/organizations/{org_id}")
JOB_COLUMNS = "id,domain,status,row_count,created_at,finished_at"


class ImportRequest(BaseModel):
    upload_id: UUID
    domain: Domain
    mapping: dict[str, str] = Field(max_length=64)


def file_record(db: Db, org_id: UUID, upload_id: UUID) -> Row[Any]:
    result = db.execute(text("SELECT * FROM app.uploads WHERE org_id=:org AND id=:id"),
                        {"org": org_id, "id": upload_id}).one_or_none()
    if result is None:
        raise HTTPException(404, "Upload not found")
    return result


def detected_type(values: list[str]) -> str:
    nonempty = [value for value in values if value]
    if not nonempty:
        return "empty"
    if all(re.fullmatch(r"-?\d+(?:\.\d+)?", value) for value in nonempty):
        return "number"
    try:
        for value in nonempty:
            date.fromisoformat(value.removesuffix(" 00:00:00"))
    except ValueError:
        return "text"
    return "date"


def preview_data(filename: str, content: bytes) -> dict[str, Any]:
    try:
        headers, rows = parse(filename, content)
    except InvalidFile as exc:
        raise HTTPException(422, str(exc)) from exc
    columns = list(zip(*rows, strict=True))
    suggestions = {}
    matches = []
    for domain in FIELDS:
        mapping, missing, ambiguous = suggest_mapping(headers, domain)
        suggestions[domain] = {"mapping": mapping, "missing": missing, "ambiguous": ambiguous}
        if not missing and not ambiguous:
            matches.append(domain)
    return {
        "detected_types": {name: detected_type(list(values)) for name, values in zip(headers, columns, strict=True)},
        "duplicate_rows": len(rows) - len({tuple(row) for row in rows}),
        "headers": headers, "rows": rows[:10], "row_count": len(rows), "fields": FIELDS,
        "suggested_domain": matches[0] if len(matches) == 1 else None, "suggestions": suggestions,
        "null_rates": {column: sum(not row[index] for row in rows) / len(rows)
                       for index, column in enumerate(headers)},
    }


@router.post("/datasets/uploads", status_code=201, dependencies=write)
def upload(org_id: UUID, db: Db, user: User, role: Admin, file: UploadFile,
           replace_upload_id: UUID | None = None) -> dict[str, Any]:
    filename = PurePosixPath((file.filename or "").replace(chr(92), "/")).name
    if len(filename) > 255 or any(ord(char) < 32 for char in filename):
        raise HTTPException(422, "Invalid filename")
    content = file.file.read(MAX_BYTES + 1)
    preview = preview_data(filename, content)
    upload_id = uuid4()
    params = {"org": org_id, "id": upload_id, "user": user, "filename": filename,
              "digest": hashlib.sha256(content).hexdigest(), "content": content, "rows": preview["row_count"]}
    if replace_upload_id is not None:
        delete_upload(org_id, replace_upload_id, db, role)
    totals = db.execute(text("SELECT count(*),COALESCE(sum(octet_length(content)),0) FROM app.uploads "
                             "WHERE org_id=:org"), params).one()
    if totals[0] >= 100 or totals[1] + len(content) > 100 * 1024**2:
        raise HTTPException(413, "Organization upload quota exceeded")
    db.execute(text("""
        INSERT INTO app.uploads (org_id,id,uploader,filename,digest,content,preview_row_count)
        VALUES (:org,:id,:user,:filename,:digest,:content,:rows)
    """), params)
    audit(db, "dataset.uploaded", org=org_id, actor=user, resource=upload_id)
    return {"id": params["id"], **preview}


@router.get("/datasets/uploads")
def uploads(org_id: UUID, db: Db, role: Admin) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(text("""
        SELECT u.id,u.filename,u.created_at,u.uploader,actor.email AS uploaded_by,
               lower(regexp_replace(u.filename,'^.*[.]','')) AS file_type,
               octet_length(u.content) AS size_bytes,COALESCE(u.preview_row_count,j.row_count) AS row_count,
               j.id AS job_id,j.domain,COALESCE(j.status,'staged') AS status,
               CASE WHEN j.status='published' THEN j.id END AS version_id,j.id IS NULL AS deletable
        FROM app.uploads u JOIN app.users actor ON actor.id=u.uploader
        LEFT JOIN LATERAL (
            SELECT id,domain,status,row_count FROM app.import_jobs
            WHERE org_id=u.org_id AND upload_id=u.id ORDER BY created_at DESC,id DESC LIMIT 1
        ) j ON true
        WHERE u.org_id=:org ORDER BY u.created_at DESC,u.id DESC
    """), {"org": org_id}).mappings()]


@router.delete("/datasets/uploads/{upload_id}", status_code=204, dependencies=write)
def delete_upload(org_id: UUID, upload_id: UUID, db: Db, role: Admin) -> None:
    params = {"org": org_id, "id": upload_id}
    deleted = db.execute(text("""
        DELETE FROM app.uploads u WHERE u.org_id=:org AND u.id=:id
        AND NOT EXISTS (SELECT 1 FROM app.import_jobs j WHERE j.org_id=u.org_id AND j.upload_id=u.id)
        RETURNING u.id
    """), params).scalar_one_or_none()
    if deleted is None:
        file_record(db, org_id, upload_id)
        raise HTTPException(409, "Upload is retained for import history")
    audit(db, "dataset.upload_deleted", org=org_id, resource=upload_id)


@router.get("/datasets/uploads/{upload_id}/preview")
def preview(org_id: UUID, upload_id: UUID, db: Db, role: Admin) -> dict[str, Any]:
    record = file_record(db, org_id, upload_id)
    return {"id": upload_id, **preview_data(record.filename, record.content)}


@router.post("/datasets/imports", status_code=202, dependencies=write)
def submit(org_id: UUID, body: ImportRequest, db: Db, user: User, role: Admin) -> dict[str, Any]:
    record = file_record(db, org_id, body.upload_id)
    headers = preview_data(record.filename, record.content)["headers"]
    if (set(body.mapping) != set(FIELDS[body.domain]) or len(set(body.mapping.values())) != len(body.mapping)
            or not set(body.mapping.values()).issubset(headers)):
        raise HTTPException(422, "Map each required field to a different source column")
    mapping = json.dumps(body.mapping, sort_keys=True)
    fingerprint = hashlib.sha256(f"{record.digest}:{mapping}".encode()).hexdigest()
    params = {"org": org_id, "id": uuid4(), "upload": body.upload_id,
              "user": user, "domain": body.domain, "map": mapping, "fingerprint": fingerprint}
    db.execute(text("INSERT INTO app.datasets (org_id,domain) VALUES (:org,:domain) ON CONFLICT DO NOTHING"), params)
    inserted = db.execute(text("""
        INSERT INTO app.import_jobs (org_id,id,upload_id,requested_by,domain,mapping,fingerprint,created_at)
        VALUES (:org,:id,:upload,:user,:domain,CAST(:map AS jsonb),:fingerprint,clock_timestamp())
        ON CONFLICT (org_id,domain,fingerprint) WHERE status <> 'cancelled' DO NOTHING RETURNING id
    """), params).scalar_one_or_none()
    job = db.execute(text(f"""
        SELECT {JOB_COLUMNS} FROM app.import_jobs
        WHERE org_id=:org AND domain=:domain AND fingerprint=:fingerprint AND status <> 'cancelled'
    """), params).mappings().one()
    db.commit()
    if job["status"] == "queued":
        enqueue(org_id, job["id"])
    return {**job, "duplicate": inserted is None}


def enqueue(org_id: UUID, job_id: UUID) -> None:
    try:
        import_snapshot.apply_async(args=(str(org_id), str(job_id)), retry=False)
    except OperationalError as exc:
        raise HTTPException(503, "Queue unavailable. Retry this import from the history when Redis is available.") from exc


@router.post("/jobs/{job_id}/retry", status_code=202, dependencies=write)
def retry_job(org_id: UUID, job_id: UUID, db: Db, role: Admin) -> dict[str, Any]:
    job = status(org_id, job_id, db, role)
    if job["status"] == "queued":
        db.commit()
        enqueue(org_id, job_id)
    return job


@router.post("/jobs/{job_id}/cancel", dependencies=write)
def cancel_job(org_id: UUID, job_id: UUID, db: Db, role: Admin) -> dict[str, Any]:
    job = status(org_id, job_id, db, role)
    if job["status"] == "cancelled":
        return job
    if job["status"] != "queued":
        raise HTTPException(409, "Only queued imports can be cancelled")
    db.execute(text("""
        UPDATE app.import_jobs SET status='cancelled',finished_at=clock_timestamp()
        WHERE org_id=:org AND id=:id AND status='queued'
    """), {"org": org_id, "id": job_id})
    audit(db, "dataset.import_cancelled", org=org_id, resource=job_id)
    return status(org_id, job_id, db, role)


@router.get("/datasets/versions")
def versions(org_id: UUID, db: Db, role: Admin) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(text("""
        SELECT v.id,v.domain,v.published_at,j.row_count,u.filename,u.id AS upload_id,a.archived_at,
               CASE WHEN d.active_version=v.id THEN 'active'
                    WHEN a.version_id IS NOT NULL THEN 'archived' ELSE 'previous' END AS status,
               d.active_version IS NULL AS can_restore
        FROM biz.dataset_versions v
        JOIN app.datasets d USING (org_id,domain)
        JOIN app.import_jobs j ON (j.org_id,j.id)=(v.org_id,v.id)
        JOIN app.uploads u ON (u.org_id,u.id)=(j.org_id,j.upload_id)
        LEFT JOIN app.archived_dataset_versions a ON (a.org_id,a.version_id)=(v.org_id,v.id)
        WHERE v.org_id=:org ORDER BY v.published_at DESC,v.id DESC
    """), {"org": org_id}).mappings()]


@router.post("/datasets/versions/{version_id}/archive", status_code=204, dependencies=write)
def archive(org_id: UUID, version_id: UUID, db: Db, role: Admin) -> None:
    params = {"org": org_id, "id": version_id}
    domain = version_domain(db, params)
    if archived(db, params):
        return
    updated = db.execute(text("""
        UPDATE app.datasets SET active_version=NULL,lifecycle_changed_at=clock_timestamp()
        WHERE org_id=:org AND domain=:domain AND active_version=:id RETURNING domain
    """), params | {"domain": domain}).scalar_one_or_none()
    if updated is None:
        raise HTTPException(409, "Only the active version can be archived")
    db.execute(text("INSERT INTO app.archived_dataset_versions(org_id,version_id) VALUES (:org,:id)"), params)
    audit(db, "dataset.archived", org=org_id, resource=version_id)


@router.post("/datasets/versions/{version_id}/restore", status_code=204, dependencies=write)
def restore(org_id: UUID, version_id: UUID, db: Db, role: Admin) -> None:
    params = {"org": org_id, "id": version_id}
    domain = version_domain(db, params)
    if not archived(db, params):
        if db.execute(text("""
            SELECT 1 FROM app.datasets WHERE org_id=:org AND domain=:domain AND active_version=:id
        """), params | {"domain": domain}).scalar_one_or_none():
            return
        raise HTTPException(409, "Only archived versions can be restored")
    updated = db.execute(text("""
        UPDATE app.datasets SET active_version=:id,lifecycle_changed_at=clock_timestamp()
        WHERE org_id=:org AND domain=:domain AND active_version IS NULL RETURNING domain
    """), params | {"domain": domain}).scalar_one_or_none()
    if updated is None:
        raise HTTPException(409, "Archive the current active version before restoring this snapshot")
    db.execute(text("DELETE FROM app.archived_dataset_versions WHERE org_id=:org AND version_id=:id"), params)
    audit(db, "dataset.restored", org=org_id, resource=version_id)


def version_domain(db: Db, params: dict[str, UUID]) -> str:
    domain: str | None = db.execute(text("""
        SELECT domain FROM biz.dataset_versions WHERE org_id=:org AND id=:id
    """), params).scalar_one_or_none()
    if domain is None:
        raise HTTPException(404, "Dataset version not found")
    return domain


def archived(db: Db, params: dict[str, UUID]) -> bool:
    return db.execute(text("""
        SELECT 1 FROM app.archived_dataset_versions WHERE org_id=:org AND version_id=:id
    """), params).scalar_one_or_none() is not None


@router.get("/datasets")
def datasets(org_id: UUID, db: Db, role: Tenant) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(text("""
        SELECT domain, active_version FROM app.datasets WHERE org_id=:org AND active_version IS NOT NULL ORDER BY domain
    """), {"org": org_id}).mappings()]


@router.get("/datasets/imports")
def history(org_id: UUID, db: Db, role: Admin, offset: int = 0) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(text(f"""
        SELECT {JOB_COLUMNS} FROM app.import_jobs WHERE org_id=:org
        ORDER BY created_at DESC,id DESC LIMIT 100 OFFSET :offset
    """), {"org": org_id, "offset": max(0, offset)}).mappings()]


@router.get("/jobs/{job_id}")
def status(org_id: UUID, job_id: UUID, db: Db, role: Admin) -> dict[str, Any]:
    job = db.execute(text(f"SELECT {JOB_COLUMNS},errors FROM app.import_jobs WHERE org_id=:org AND id=:id"),
                     {"org": org_id, "id": job_id}).mappings().one_or_none()
    if job is None:
        raise HTTPException(404, "Import not found")
    return dict(job)
