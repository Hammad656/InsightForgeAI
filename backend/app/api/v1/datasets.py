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
from app.ingestion import FIELDS, MAX_BYTES, Domain, InvalidFile, parse
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
    return {
        "detected_types": {name: detected_type(list(values)) for name, values in zip(headers, columns, strict=True)},
        "duplicate_rows": len(rows) - len({tuple(row) for row in rows}),
        "headers": headers, "rows": rows[:10], "row_count": len(rows), "fields": FIELDS,
        "null_rates": {column: sum(not row[index] for row in rows) / len(rows)
                       for index, column in enumerate(headers)},
    }


@router.post("/datasets/uploads", status_code=201, dependencies=write)
def upload(org_id: UUID, db: Db, user: User, role: Admin, file: UploadFile) -> dict[str, Any]:
    filename = PurePosixPath((file.filename or "").replace(chr(92), "/")).name
    if len(filename) > 255 or any(ord(char) < 32 for char in filename):
        raise HTTPException(422, "Invalid filename")
    content = file.file.read(MAX_BYTES + 1)
    preview = preview_data(filename, content)
    params = {"org": org_id, "id": uuid4(), "user": user, "filename": filename,
              "digest": hashlib.sha256(content).hexdigest(), "content": content}
    totals = db.execute(text("SELECT count(*),COALESCE(sum(octet_length(content)),0) FROM app.uploads "
                             "WHERE org_id=:org"), params).one()
    if totals[0] >= 100 or totals[1] + len(content) > 100 * 1024**2:
        raise HTTPException(413, "Organization upload quota exceeded")
    db.execute(text("""
        INSERT INTO app.uploads (org_id,id,uploader,filename,digest,content)
        VALUES (:org,:id,:user,:filename,:digest,:content)
    """), params)
    return {"id": params["id"], **preview}


@router.get("/datasets/uploads/{upload_id}/preview")
def preview(org_id: UUID, upload_id: UUID, db: Db, role: Admin) -> dict[str, Any]:
    record = file_record(db, org_id, upload_id)
    return preview_data(record.filename, record.content)


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
        INSERT INTO app.import_jobs (org_id,id,upload_id,requested_by,domain,mapping,fingerprint)
        VALUES (:org,:id,:upload,:user,:domain,CAST(:map AS jsonb),:fingerprint)
        ON CONFLICT (org_id,domain,fingerprint) DO NOTHING RETURNING id
    """), params).scalar_one_or_none()
    job = db.execute(text(f"""
        SELECT {JOB_COLUMNS} FROM app.import_jobs
        WHERE org_id=:org AND domain=:domain AND fingerprint=:fingerprint
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
