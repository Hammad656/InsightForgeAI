"""Admin-only connector configuration, discovery and sync history."""
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from kombu.exceptions import OperationalError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.api.v1.auth import Db, User, limit_key
from app.api.v1.organizations import Admin, write
from app.audit import audit
from app.ingestion import FIELDS
from app.jobs.connectors import sync_postgres
from app.pg_connector import ConnectorError, Object, ProfileKey, Selection, columns, extract, profile, profiles, remote
from app.reconciliation import REQUIRED

router = APIRouter(prefix="/organizations/{org_id}/connectors")
PUBLIC = "id,label,profile,enabled,last_sync_at"
JOB = "id,status,created_at,finished_at,row_count,error_code,version_id,recon_source_id"


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=100, pattern=r"^[^\x00]+$")
    profile: ProfileKey


class State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class Sync(Selection):
    request_key: UUID


def source_error(exc: ConnectorError) -> HTTPException:
    status = 503 if exc.code in {"connector_busy", "source_connection_failed", "query_timeout"} else 422
    return HTTPException(status, exc.code)


def record(db: Db, org: UUID, connector: UUID, *, active: bool = False) -> dict[str, Any]:
    row = db.execute(text(f"""
      SELECT {PUBLIC} FROM app.pg_connectors WHERE org_id=:org AND id=:id AND NOT removed
    """), {"org": org, "id": connector}).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "Connector not found")
    if active and not row["enabled"]:
        raise HTTPException(409, "Connector is disabled")
    return dict(row)


@router.get("/profiles")
def available(org_id: UUID, db: Db, role: Admin) -> dict[str, Any]:
    items = []
    for key in sorted(profiles(org_id)):
        try:
            item = profile(org_id, key)
        except ConnectorError:
            continue
        items.append({"key": key, "host": item.host, "port": item.port, "database": item.database,
                      "security": "isolated loopback" if item.isolated_loopback and not item.ca_file else "TLS verify-full"})
    return {"profiles": items, "analytics_fields": FIELDS,
            "reconciliation_fields": {key: sorted(value) for key, value in REQUIRED.items()}}


@router.get("")
def listing(org_id: UUID, db: Db, role: Admin) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(text(f"""
      SELECT {PUBLIC} FROM app.pg_connectors WHERE org_id=:org AND NOT removed ORDER BY label,id LIMIT 50
    """), {"org": org_id}).mappings()]


@router.post("", status_code=201, dependencies=write)
def create(org_id: UUID, db: Db, role: Admin, body: Create) -> dict[str, Any]:
    try:
        profile(org_id, body.profile)
    except ConnectorError as exc:
        raise source_error(exc) from None
    count = db.execute(text("SELECT count(*) FROM app.pg_connectors WHERE org_id=:org AND NOT removed"),
                       {"org": org_id}).scalar_one()
    if count >= 25:
        raise HTTPException(409, "Connector quota reached")
    identity = uuid4()
    db.execute(text("""
      INSERT INTO app.pg_connectors(org_id,id,label,profile) VALUES (:org,:id,:label,:profile)
    """), {"org": org_id, "id": identity, **body.model_dump()})
    audit(db, "connector.created", org=org_id, resource=identity)
    return record(db, org_id, identity)


@router.patch("/{connector}", dependencies=write)
def update(org_id: UUID, db: Db, role: Admin, connector: UUID, body: State) -> dict[str, Any]:
    record(db, org_id, connector)
    db.execute(text("UPDATE app.pg_connectors SET enabled=:enabled WHERE org_id=:org AND id=:id"),
               {"org": org_id, "id": connector, "enabled": body.enabled})
    audit(db, "connector.enabled" if body.enabled else "connector.disabled", org=org_id, resource=connector)
    return record(db, org_id, connector)


@router.delete("/{connector}", status_code=204, dependencies=write)
def remove(org_id: UUID, db: Db, role: Admin, connector: UUID) -> None:
    record(db, org_id, connector)
    db.execute(text("UPDATE app.pg_connectors SET enabled=false,removed=true WHERE org_id=:org AND id=:id"),
               {"org": org_id, "id": connector})
    audit(db, "connector.removed", org=org_id, resource=connector)


@router.post("/{connector}/test", dependencies=write)
def test_connection(org_id: UUID, db: Db, user: User, role: Admin, connector: UUID) -> dict[str, Any]:
    row = record(db, org_id, connector, active=True)
    limit_key(f"if:connector-interactive:{user}", 30)
    try:
        item = profile(org_id, row["profile"])
        with remote(item) as conn:
            for target in item.objects:
                if not columns(conn, item, target):
                    raise ConnectorError("source_objects_unavailable")
    except ConnectorError as exc:
        raise source_error(exc) from None
    audit(db, "connector.tested", org=org_id, resource=connector)
    return {"status": "verified", "objects": item.objects}


@router.post("/{connector}/columns", dependencies=write)
def discover(org_id: UUID, db: Db, user: User, role: Admin, connector: UUID, body: Object) -> list[dict[str, str]]:
    row = record(db, org_id, connector, active=True)
    limit_key(f"if:connector-interactive:{user}", 30)
    try:
        item = profile(org_id, row["profile"])
        with remote(item) as conn:
            return columns(conn, item, body)
    except ConnectorError as exc:
        raise source_error(exc) from None


@router.post("/{connector}/preview", dependencies=write)
def preview(org_id: UUID, db: Db, user: User, role: Admin, connector: UUID, body: Selection) -> dict[str, Any]:
    row = record(db, org_id, connector, active=True)
    limit_key(f"if:connector-interactive:{user}", 30)
    try:
        headers, rows, _ = extract(profile(org_id, row["profile"]), body, preview=True)
    except ConnectorError as exc:
        raise source_error(exc) from None
    audit(db, "connector.previewed", org=org_id, resource=connector)
    return {"headers": headers, "rows": rows}


def enqueue(org: UUID, job: UUID) -> None:
    try:
        sync_postgres.apply_async(args=(str(org), str(job)), retry=False)
    except OperationalError:
        return


@router.post("/{connector}/syncs", status_code=202, dependencies=write)
def submit(org_id: UUID, db: Db, user: User, role: Admin, connector: UUID, body: Sync) -> dict[str, Any]:
    row = record(db, org_id, connector, active=True)
    limit_key(f"if:connector-sync:{user}", 10)
    try:
        item = profile(org_id, row["profile"])
        if not any((target.schema_name, target.table) == (body.schema_name, body.table) for target in item.objects):
            raise ConnectorError("source_object_denied")
    except ConnectorError as exc:
        raise source_error(exc) from None
    config = body.model_dump(mode="json", exclude={"request_key"})
    args = {"org": org_id, "con": connector, "id": uuid4(), "key": body.request_key,
            "actor": user, "config": body.model_dump_json(exclude={"request_key"})}
    inserted = db.execute(text("""
      INSERT INTO app.pg_syncs(org_id,id,connector_id,actor_id,request_key,config)
      VALUES (:org,:id,:con,:actor,:key,CAST(:config AS jsonb))
      ON CONFLICT(org_id,connector_id,request_key) DO NOTHING RETURNING id
    """), args).scalar_one_or_none()
    job = db.execute(text("""
      SELECT id,status,config,actor_id FROM app.pg_syncs WHERE org_id=:org AND connector_id=:con AND request_key=:key
    """), args).mappings().one()
    if job["config"] != config or job["actor_id"] != user:
        raise HTTPException(409, "Sync request key already used")
    if inserted:
        audit(db, "connector.sync_requested", org=org_id, resource=inserted)
    db.commit()
    if job["status"] == "queued":
        enqueue(org_id, job["id"])
    return {"id": job["id"], "status": job["status"]}


@router.get("/{connector}/syncs")
def history(org_id: UUID, db: Db, role: Admin, connector: UUID) -> list[dict[str, Any]]:
    record(db, org_id, connector)
    return [dict(row) for row in db.execute(text(f"""
      SELECT {JOB},status='queued' OR status='running' AND lease_until<=clock_timestamp() AS recoverable
      FROM app.pg_syncs WHERE org_id=:org AND connector_id=:id ORDER BY created_at DESC LIMIT 50
    """), {"org": org_id, "id": connector}).mappings()]


@router.post("/{connector}/syncs/{job}/retry", status_code=202, dependencies=write)
def retry(org_id: UUID, db: Db, user: User, role: Admin, connector: UUID, job: UUID) -> dict[str, UUID]:
    record(db, org_id, connector, active=True)
    limit_key(f"if:connector-sync:{user}", 10)
    updated = db.execute(text("""
      UPDATE app.pg_syncs SET status='queued',attempts=0,lease_token=NULL,lease_until=NULL,error_code=NULL,finished_at=NULL
      WHERE org_id=:org AND connector_id=:con AND id=:id AND actor_id=:actor
        AND (status IN ('queued','failed') OR status='running' AND lease_until<=clock_timestamp())
      RETURNING id
    """), {"org": org_id, "con": connector, "id": job, "actor": user}).scalar_one_or_none()
    if not updated:
        raise HTTPException(404, "Retryable sync not found")
    audit(db, "connector.sync_retried", org=org_id, resource=job)
    db.commit()
    enqueue(org_id, job)
    return {"id": job}
