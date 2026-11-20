"""Submit, poll, cancel and inspect an author's multi-domain analyses."""
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from kombu.exceptions import OperationalError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.api.v1.auth import Db, User, limit_key
from app.api.v1.organizations import locked_org, tenant, write
from app.audit import audit, correlation_id
from app.data_quality import quality
from app.jobs.analyses import encoded, run_analysis

router = APIRouter(prefix="/ai/analyses")
VISIBLE = """
    id,question,correlation_id,status,created_at,started_at,finished_at,business_day,
    plan,tasks,summary,planner_tokens,data_quality
"""


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    organization_id: UUID
    request_key: UUID
    question: str = Field(min_length=3, max_length=500)
    dataset_ids: list[UUID] = Field(default_factory=list, max_length=3)


def authorize(org: UUID, db: Db, user: UUID) -> None:
    if tenant(org, db, user) == "viewer":
        raise HTTPException(403, "Analyst or administrator permission required")


def enqueue(org: UUID, user: UUID, analysis: UUID) -> None:
    try:
        run_analysis.apply_async(args=(str(org), str(user), str(analysis)), retry=False, expires=120)
    except OperationalError:
        # Resubmitting the same request key can dispatch the committed job again.
        return


def expire(db: Db, org: UUID, user: UUID, analysis: UUID | None = None) -> None:
    rows = db.execute(text("""
        UPDATE app.ai_analyses SET status='timed_out',finished_at=clock_timestamp(),
            summary='{"message":"The analysis exceeded its execution deadline."}'
        WHERE org_id=:org AND author_id=:author AND (CAST(:id AS uuid) IS NULL OR id=:id)
        AND ((status='running' AND deadline_at<=clock_timestamp()) OR
             (status='queued' AND created_at<=clock_timestamp()-interval '2 minutes'))
        RETURNING id,correlation_id
    """), {"org": org, "author": user, "id": analysis})
    for row in rows:
        audit(db, "analysis.timed_out", org=org, actor=user, resource=row.id, correlation=row.correlation_id)


def view(db: Db, org: UUID, user: UUID, analysis: UUID) -> dict[str, Any]:
    result = db.execute(text(f"""
        SELECT {VISIBLE} FROM app.ai_analyses WHERE org_id=:org AND author_id=:author AND id=:id
    """), {"org": org, "author": user, "id": analysis}).mappings().one_or_none()
    if result is None:
        raise HTTPException(404, "Analysis not found")
    value = dict(result)
    if value["status"] in {"cancelled", "timed_out", "failed"}:
        value["tasks"] = {
            key: {**task, "status": value["status"], "answer": "Execution stopped before a result was published."}
            if task["status"] == "running" else task for key, task in value["tasks"].items()
        }
    return value


@router.post("", status_code=202, dependencies=write)
def submit(body: AnalysisRequest, db: Db, user: User) -> dict[str, Any]:
    org = body.organization_id
    locked_org(db, org)
    authorize(org, db, user)
    datasets = sorted(set(body.dataset_ids))
    if len(datasets) != len(body.dataset_ids) or "\x00" in body.question:
        raise HTTPException(422, "Invalid question or dataset scope")
    params = {"org": org, "author": user, "key": body.request_key}
    existing = db.execute(text("""
        SELECT id,question,dataset_ids FROM app.ai_analyses
        WHERE org_id=:org AND author_id=:author AND request_key=:key
    """), params).one_or_none()
    if existing:
        if existing.question != body.question or existing.dataset_ids != datasets:
            raise HTTPException(409, "This request key belongs to a different analysis")
        analysis = existing.id
        expire(db, org, user, analysis)
    else:
        active = db.execute(text("""
            SELECT d.domain,v.id AS version,v.published_at
            FROM app.datasets d JOIN biz.dataset_versions v
              ON (v.org_id,v.domain,v.id)=(d.org_id,d.domain,d.active_version)
            WHERE d.org_id=:org
        """), params).mappings().all()
        if datasets and not set(datasets).issubset({row["version"] for row in active}):
            raise HTTPException(404, "Authorized active snapshot not found")
        limit_key(f"if:analysis:{user}", 6)
        sources = {row["domain"]: {"version": row["version"], "published_at": row["published_at"]}
                   for row in active if not datasets or row["version"] in datasets}
        trace = correlation_id()
        analysis = db.execute(text("""
            INSERT INTO app.ai_analyses
                (org_id,author_id,id,request_key,correlation_id,question,dataset_ids,sources,data_quality,business_day)
            VALUES (:org,:author,:id,:key,:trace,:question,:datasets,CAST(:sources AS jsonb),CAST(:quality AS jsonb),
                    (now() AT TIME ZONE 'Asia/Karachi')::date)
            RETURNING id
        """), {**params, "id": uuid4(), "trace": trace, "question": body.question, "datasets": datasets,
               "sources": encoded(sources), "quality": encoded(quality(db, org))}).scalar_one()
        audit(db, "analysis.requested", org=org, actor=user, resource=analysis, correlation=trace)
    result = view(db, org, user, analysis)
    db.commit()
    if result["status"] == "queued":
        enqueue(org, user, analysis)
    return result


@router.get("")
def history(organization_id: UUID, db: Db, user: User, limit: int = Query(20, ge=1, le=50)) -> list[dict[str, Any]]:
    authorize(organization_id, db, user)
    expire(db, organization_id, user)
    return [dict(row) for row in db.execute(text("""
        SELECT id,question,status,created_at,finished_at FROM app.ai_analyses
        WHERE org_id=:org AND author_id=:author ORDER BY created_at DESC,id DESC LIMIT :limit
    """), {"org": organization_id, "author": user, "limit": limit}).mappings()]


@router.get("/{analysis_id}")
def detail(analysis_id: UUID, organization_id: UUID, db: Db, user: User) -> dict[str, Any]:
    authorize(organization_id, db, user)
    expire(db, organization_id, user, analysis_id)
    return view(db, organization_id, user, analysis_id)


@router.post("/{analysis_id}/cancel", dependencies=write)
def cancel(analysis_id: UUID, organization_id: UUID, db: Db, user: User) -> dict[str, Any]:
    authorize(organization_id, db, user)
    expire(db, organization_id, user, analysis_id)
    trace = db.execute(text("""
        UPDATE app.ai_analyses SET status='cancelled',finished_at=clock_timestamp(),
            summary='{"message":"You cancelled this analysis."}'
        WHERE org_id=:org AND author_id=:author AND id=:id AND status IN ('queued','running')
        RETURNING correlation_id
    """), {"org": organization_id, "author": user, "id": analysis_id}).scalar_one_or_none()
    if trace:
        audit(db, "analysis.cancelled", org=organization_id, actor=user, resource=analysis_id, correlation=trace)
    return view(db, organization_id, user, analysis_id)
