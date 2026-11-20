"""Tenant-scoped source ingestion, deterministic runs and audited reviews."""
import hashlib
import json
from datetime import date
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Form, HTTPException, Query, UploadFile
from kombu.exceptions import OperationalError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, text

from app.api.v1.auth import Db, User
from app.api.v1.organizations import admin, locked_org, tenant, write
from app.audit import audit, correlation_id
from app.data_quality import quality
from app.ingestion import MAX_BYTES, InvalidFile, parse
from app.jobs.reconciliation import reconcile_sources
from app.recon_models import ReconciliationIssue, ReconciliationRun
from app.reconciliation import REQUIRED, Domain, Kind, SourceOptions, normalize_key

router = APIRouter()
SOURCE_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,39}$"
SOURCE_FIELDS = "id,source,domain,revision,filename,digest,options,created_at,jsonb_array_length(records) AS row_count"


class AliasBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(pattern=SOURCE_PATTERN)
    kind: Kind
    source_key: str = Field(min_length=1, max_length=120)
    canonical_key: str = Field(min_length=1, max_length=120)
    expected_revision: int = Field(default=0, ge=0)


class RunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: UUID
    request_key: UUID
    input_ids: list[UUID] = Field(min_length=3, max_length=8)
    as_of: date


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["reviewed", "resolved", "acknowledged"]
    note: str = Field(min_length=5, max_length=500)
    reviewer: UUID | None = None
    correction_alias_id: UUID | None = None


def enqueue(org: UUID, run: UUID) -> None:
    try:
        reconcile_sources.apply_async(args=(str(org), str(run)), retry=False)
    except OperationalError:
        # The committed job remains queued and can be dispatched from history.
        return


@router.post("/reconciliation-sources", status_code=201, dependencies=write)
def upload(
    db: Db, user: User, file: UploadFile, organization_id: Annotated[UUID, Form()],
    source: Annotated[str, Form(pattern=SOURCE_PATTERN)], domain: Annotated[Domain, Form()],
    currency: Annotated[str, Form()] = "PKR", tax_basis: Annotated[str, Form()] = "gross",
    invoice_source: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    admin(organization_id, db, user)
    filename = PurePosixPath((file.filename or "").replace(chr(92), "/")).name
    if not filename or len(filename) > 255 or any(ord(char) < 32 for char in filename):
        raise HTTPException(422, "Invalid filename")
    data = file.file.read(MAX_BYTES + 1)
    try:
        options = SourceOptions.model_validate({"currency": currency, "tax_basis": tax_basis, "invoice_source": invoice_source})
        headers, records = parse(filename, data)
    except (InvalidFile, ValidationError) as exc:
        raise HTTPException(422, "Invalid reconciliation file or source metadata") from exc
    if not REQUIRED[domain].issubset(headers):
        raise HTTPException(422, "Missing columns: " + ", ".join(sorted(REQUIRED[domain] - set(headers))))
    params = {"org": organization_id, "actor": user, "id": uuid4(), "source": source, "domain": domain,
              "filename": filename, "digest": hashlib.sha256(data).hexdigest(), "original": data,
              "header": json.dumps(headers), "records": json.dumps(records), "options": options.model_dump_json()}
    previous = db.execute(text(f"""
        SELECT {SOURCE_FIELDS} FROM app.recon_sources WHERE org_id=:org AND source=:source AND domain=:domain
        AND digest=:digest AND options=CAST(:options AS jsonb) ORDER BY revision DESC LIMIT 1
    """), params).mappings().one_or_none()
    if previous:
        return dict(previous)
    count, size = db.execute(text("""
        SELECT count(*),COALESCE(sum(octet_length(original)),0) FROM app.recon_sources WHERE org_id=:org
    """), params).one()
    if count >= 64 or size + len(data) > 100 * 1024**2:
        raise HTTPException(413, "Reconciliation source quota exceeded")
    row = db.execute(text(f"""
        INSERT INTO app.recon_sources
          (org_id,id,source,domain,revision,filename,digest,original,header,records,options,actor_id)
        SELECT :org,:id,:source,:domain,COALESCE(max(revision),0)+1,:filename,:digest,:original,
               CAST(:header AS jsonb),CAST(:records AS jsonb),CAST(:options AS jsonb),:actor
        FROM app.recon_sources WHERE org_id=:org AND source=:source AND domain=:domain RETURNING {SOURCE_FIELDS}
    """), params).mappings().one()
    audit(db, "reconciliation.source_uploaded", org=organization_id, actor=user, resource=row["id"])
    return dict(row)


@router.get("/reconciliation-sources")
def sources(organization_id: UUID, db: Db, user: User) -> list[dict[str, Any]]:
    tenant(organization_id, db, user)
    return [dict(row) for row in db.execute(text(f"""
        SELECT {SOURCE_FIELDS} FROM app.recon_sources WHERE org_id=:org ORDER BY created_at DESC,id DESC
    """), {"org": organization_id}).mappings()]


@router.post("/reconciliation-mappings", status_code=201, dependencies=write)
def set_alias(body: AliasBody, organization_id: UUID, db: Db, user: User) -> dict[str, Any]:
    admin(organization_id, db, user)
    try:
        source_key, canonical_key = normalize_key(body.source_key), normalize_key(body.canonical_key)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    params = {"org": organization_id, "actor": user, "id": uuid4(), "new_entity": uuid4(),
              "source": body.source, "kind": body.kind, "key": source_key, "canonical": canonical_key}
    previous = db.execute(text("""
        SELECT a.revision FROM app.recon_aliases a WHERE org_id=:org AND source=:source AND kind=:kind AND source_key=:key
        ORDER BY revision DESC LIMIT 1
    """), params).scalar_one_or_none()
    if (previous or 0) != body.expected_revision:
        raise HTTPException(409, "Mapping revision changed. Reload its history before making a correction.")
    if db.execute(text("SELECT count(*) FROM app.recon_aliases WHERE org_id=:org"), params).scalar_one() >= 10_000:
        raise HTTPException(413, "Mapping history quota exceeded")
    db.execute(text("""
        INSERT INTO app.recon_entities(org_id,id,kind,canonical_key) VALUES(:org,:new_entity,:kind,:canonical)
        ON CONFLICT(org_id,kind,canonical_key) DO NOTHING
    """), params)
    row = db.execute(text("""
        INSERT INTO app.recon_aliases(org_id,id,source,kind,source_key,revision,entity_id,actor_id)
        SELECT :org,:id,:source,:kind,:key,:revision,id,:actor
        FROM app.recon_entities WHERE org_id=:org AND kind=:kind AND canonical_key=:canonical
        RETURNING id,source,kind,source_key,revision,created_at
    """), {**params, "revision": (previous or 0) + 1}).mappings().one()
    audit(db, "reconciliation.mapping_versioned", org=organization_id, actor=user, resource=row["id"])
    return {**row, "canonical_key": canonical_key}


@router.get("/reconciliation-mappings")
def mappings(
    organization_id: UUID, db: Db, user: User, offset: int = Query(0, ge=0, le=10_000),
) -> list[dict[str, Any]]:
    tenant(organization_id, db, user)
    return [dict(row) for row in db.execute(text("""
        SELECT a.id,a.source,a.kind,a.source_key,a.revision,a.created_at,e.canonical_key
        FROM app.recon_aliases a JOIN app.recon_entities e ON (e.org_id,e.kind,e.id)=(a.org_id,a.kind,a.entity_id)
        WHERE a.org_id=:org ORDER BY a.created_at DESC,a.id DESC LIMIT 100 OFFSET :offset
    """), {"org": organization_id, "offset": offset}).mappings()]


def run_row(db: Db, org: UUID, run: UUID) -> dict[str, Any]:
    result = db.execute(select(ReconciliationRun).where(
        ReconciliationRun.org_id == org, ReconciliationRun.id == run,
    )).mappings().one_or_none()
    if result is None:
        raise HTTPException(404, "Reconciliation run not found")
    response = {key: value for key, value in result.items() if key != "alias_ids"}
    if response["summary"] and "comparison" in response["summary"]:
        comparisons = response["summary"]["comparison"]
        response["summary"] = {**response["summary"], "comparison": comparisons[:100],
                               "comparison_count": len(comparisons)}
    return response


@router.post("/reconciliation-runs", status_code=202, dependencies=write)
def start(body: RunBody, db: Db, user: User) -> dict[str, Any]:
    org = body.organization_id
    locked_org(db, org)
    if tenant(org, db, user) == "viewer":
        raise HTTPException(403, "Analyst or administrator permission required")
    inputs = sorted(set(body.input_ids))
    if len(inputs) != len(body.input_ids):
        raise HTTPException(422, "Source versions must be unique")
    params = {"org": org, "key": body.request_key, "actor": user, "id": uuid4(), "inputs": inputs,
              "as_of": body.as_of, "correlation": correlation_id()}
    existing = db.execute(text("""
        SELECT id,input_ids,as_of FROM app.recon_runs WHERE org_id=:org AND request_key=:key
    """), params).one_or_none()
    if existing is not None:
        if existing.input_ids != inputs or existing.as_of != body.as_of:
            raise HTTPException(409, "This idempotency key belongs to a different reconciliation")
        run = existing.id
    else:
        available = db.execute(text("""
            SELECT domain FROM app.recon_sources WHERE org_id=:org AND id=ANY(:inputs)
        """), params).scalars().all()
        if len(available) != len(inputs):
            raise HTTPException(404, "Source version not found")
        if not {"sales", "inventory", "payments"}.issubset(available):
            raise HTTPException(422, "Select at least one sales, inventory and payment source")
        run = db.execute(text("""
            INSERT INTO app.recon_runs(org_id,id,actor_id,request_key,input_ids,alias_ids,as_of,correlation_id)
            SELECT :org,:id,:actor,:key,:inputs,ARRAY(
                SELECT DISTINCT ON (source,kind,source_key) id FROM app.recon_aliases WHERE org_id=:org
                ORDER BY source,kind,source_key,revision DESC
            ),:as_of,:correlation RETURNING id
        """), params).scalar_one()
        audit(db, "reconciliation.requested", org=org, actor=user, resource=run)
    response = run_row(db, org, run)
    db.commit()
    if response["status"] == "queued":
        enqueue(org, run)
    return response


@router.get("/reconciliation-runs")
def history(organization_id: UUID, db: Db, user: User) -> list[dict[str, Any]]:
    tenant(organization_id, db, user)
    return [dict(row) for row in db.execute(text("""
        SELECT id,input_ids,as_of,status,created_at,finished_at,summary->'issue_count' AS issue_count
        FROM app.recon_runs WHERE org_id=:org ORDER BY created_at DESC,id DESC LIMIT 50
    """), {"org": organization_id}).mappings()]


@router.get("/reconciliation-summary")
def summary(organization_id: UUID, db: Db, user: User) -> dict[str, Any] | None:
    tenant(organization_id, db, user)
    return quality(db, organization_id)


@router.get("/reconciliation-runs/{run_id}")
def detail(run_id: UUID, organization_id: UUID, db: Db, user: User) -> dict[str, Any]:
    tenant(organization_id, db, user)
    return run_row(db, organization_id, run_id)


@router.post("/reconciliation-runs/{run_id}/retry", status_code=202, dependencies=write)
def retry(run_id: UUID, organization_id: UUID, db: Db, user: User) -> dict[str, Any]:
    org = organization_id
    locked_org(db, org)
    role = tenant(org, db, user)
    if role == "viewer":
        raise HTTPException(403, "Analyst or administrator permission required")
    run = run_row(db, org, run_id)
    if run["actor_id"] != user and role != "admin":
        raise HTTPException(403, "Only the requester or an administrator can retry this run")
    if run["status"] == "failed":
        db.execute(text("""
            UPDATE app.recon_runs SET status='queued',finished_at=NULL,summary=NULL
            WHERE org_id=:org AND id=:run AND status='failed'
        """), {"org": org, "run": run_id})
        audit(db, "reconciliation.retried", org=org, actor=user, resource=run_id)
        run = run_row(db, org, run_id)
    db.commit()
    if run["status"] == "queued":
        enqueue(org, run_id)
    return run


@router.get("/reconciliation-runs/{run_id}/mappings")
def run_mappings(
    run_id: UUID, organization_id: UUID, db: Db, user: User, offset: int = Query(0, ge=0, le=10_000),
) -> list[dict[str, Any]]:
    tenant(organization_id, db, user)
    run_row(db, organization_id, run_id)
    return [dict(row) for row in db.execute(text("""
        SELECT a.id,a.source,a.kind,a.source_key,a.revision,e.canonical_key
        FROM app.recon_runs r JOIN app.recon_aliases a ON a.org_id=r.org_id AND a.id=ANY(r.alias_ids)
        JOIN app.recon_entities e ON (e.org_id,e.kind,e.id)=(a.org_id,a.kind,a.entity_id)
        WHERE r.org_id=:org AND r.id=:run ORDER BY a.source,a.kind,a.source_key LIMIT 100 OFFSET :offset
    """), {"org": organization_id, "run": run_id, "offset": offset}).mappings()]


@router.get("/reconciliation-runs/{run_id}/comparison")
def comparison(
    run_id: UUID, organization_id: UUID, db: Db, user: User, offset: int = Query(0, ge=0, le=80_000),
) -> list[dict[str, Any]]:
    tenant(organization_id, db, user)
    run_row(db, organization_id, run_id)
    return list(db.execute(text("""
        SELECT items.value FROM app.recon_runs r CROSS JOIN LATERAL jsonb_array_elements(r.summary->'comparison')
            WITH ORDINALITY AS items(value,position)
        WHERE r.org_id=:org AND r.id=:run ORDER BY items.position LIMIT 100 OFFSET :offset
    """), {"org": organization_id, "run": run_id, "offset": offset}).scalars())


@router.get("/reconciliation-runs/{run_id}/issues")
def issues(
    run_id: UUID, organization_id: UUID, db: Db, user: User, offset: int = Query(0, ge=0, le=500_000),
) -> list[dict[str, Any]]:
    tenant(organization_id, db, user)
    run_row(db, organization_id, run_id)
    return [dict(row) for row in db.execute(select(ReconciliationIssue).where(
        ReconciliationIssue.org_id == organization_id, ReconciliationIssue.run_id == run_id,
    ).order_by(ReconciliationIssue.severity, ReconciliationIssue.rule, ReconciliationIssue.id)
        .limit(100).offset(offset)).mappings()]


def issue_row(db: Db, org: UUID, issue: UUID, *, lock: bool = False) -> dict[str, Any]:
    query = select(ReconciliationIssue).where(ReconciliationIssue.org_id == org, ReconciliationIssue.id == issue)
    row = db.execute(query.with_for_update() if lock else query).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Reconciliation exception not found")
    return dict(row)


@router.get("/reconciliation-issues/{issue_id}/reviews")
def review_history(issue_id: UUID, organization_id: UUID, db: Db, user: User) -> list[dict[str, Any]]:
    tenant(organization_id, db, user)
    issue_row(db, organization_id, issue_id)
    return [dict(row) for row in db.execute(text("""
        SELECT * FROM app.recon_reviews WHERE org_id=:org AND issue_id=:issue ORDER BY created_at,id LIMIT 100
    """), {"org": organization_id, "issue": issue_id}).mappings()]


@router.patch("/reconciliation-issues/{issue_id}", dependencies=write)
def review(issue_id: UUID, organization_id: UUID, body: ReviewBody, db: Db, user: User) -> dict[str, Any]:
    org = organization_id
    locked_org(db, org)
    role = tenant(org, db, user)
    if role == "viewer" or (role != "admin" and body.status != "reviewed"):
        raise HTTPException(403, "Review action is outside your role")
    current = issue_row(db, org, issue_id, lock=True)
    if current["status"] in {"resolved", "acknowledged"}:
        raise HTTPException(409, "This exception already has a final disposition")
    if body.status != "reviewed" and current["status"] != "reviewed":
        raise HTTPException(409, "Review the exception before recording its disposition")
    if "\x00" in body.note:
        raise HTTPException(422, "Invalid review note")
    if role != "admin" and (current["reviewer"] not in {None, user} or body.reviewer not in {None, user}
                            or body.correction_alias_id):
        raise HTTPException(403, "Only administrators can reassign a review or reference a correction")
    reviewer = body.reviewer or current["reviewer"] or user
    params = {"org": org, "id": uuid4(), "issue": issue_id, "actor": user, "before": current["status"],
              "status": body.status, "note": body.note, "reviewer": reviewer, "alias": body.correction_alias_id,
              "correlation": correlation_id()}
    if db.execute(text("""
        SELECT 1 FROM app.memberships WHERE org_id=:org AND user_id=:reviewer AND role IN ('admin','analyst')
    """), params).scalar_one_or_none() is None:
        raise HTTPException(404, "Eligible reviewer not found")
    if body.correction_alias_id and db.execute(text("""
        SELECT 1 FROM app.recon_aliases WHERE org_id=:org AND id=:alias
    """), params).scalar_one_or_none() is None:
        raise HTTPException(404, "Correction mapping not found")
    if body.status == "resolved" and current["severity"] == "material":
        corrected = db.execute(text("""
            SELECT 1 FROM app.recon_runs newer JOIN app.recon_runs original ON original.org_id=newer.org_id
              AND original.id=:run
            WHERE newer.org_id=:org AND newer.status='completed' AND newer.created_at>original.created_at
              AND newer.as_of>=original.as_of
              AND NOT EXISTS (
                SELECT 1 FROM app.recon_sources old WHERE old.org_id=:org AND old.id=ANY(original.input_ids)
                AND NOT EXISTS (
                    SELECT 1 FROM app.recon_sources fresh WHERE fresh.org_id=:org AND fresh.id=ANY(newer.input_ids)
                    AND (fresh.source,fresh.domain)=(old.source,old.domain) AND fresh.revision>=old.revision
                )
              )
              AND NOT EXISTS (
                SELECT 1 FROM app.recon_issues check_issue WHERE check_issue.org_id=:org AND check_issue.run_id=newer.id
                  AND check_issue.rule=:rule AND check_issue.severity='material'
              ) LIMIT 1
        """), {**params, "run": current["run_id"], "rule": current["rule"]}).scalar_one_or_none()
        if corrected is None:
            raise HTTPException(409, "Resolve material exceptions only after a corrected reconciliation of the same sources")
    db.execute(text("""
        UPDATE app.recon_issues SET status=:status,reviewer=:reviewer,updated_at=now() WHERE org_id=:org AND id=:issue
    """), params)
    db.execute(text("""
        INSERT INTO app.recon_reviews(org_id,id,issue_id,actor_id,previous_status,status,note,reviewer,
                                      correction_alias_id,correlation_id)
        VALUES(:org,:id,:issue,:actor,:before,:status,:note,:reviewer,:alias,:correlation)
    """), params)
    audit(db, "reconciliation.exception_" + body.status, org=org, actor=user, resource=issue_id)
    return issue_row(db, org, issue_id)
