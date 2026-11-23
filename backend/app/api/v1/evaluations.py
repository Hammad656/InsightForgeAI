"""Private evaluation history, baseline comparison and synthetic-safe JSON export."""
import json
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from kombu.exceptions import OperationalError
from pydantic import BaseModel, ConfigDict
from sqlalchemy import RowMapping, text

from app.api.v1.auth import Db, User, limit_key
from app.api.v1.organizations import Role, locked_org, tenant, write
from app.audit import audit
from app.core.config import get_settings
from app.eval_fixtures import SUITE
from app.evaluation import MODEL, live_available, manifest
from app.jobs.evaluations import evaluate_run

router = APIRouter(prefix="/evaluations")


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    suite: Literal["retail-holdout-v1"] = "retail-holdout-v1"
    live: bool = False
    approve_provider_cost: bool = False


def scope(db: Db, org: UUID, user: UUID, admin: bool = False) -> Role:
    role = tenant(org, db, user)
    if role == "viewer" or admin and role != "admin":
        raise HTTPException(403, "Evaluation permission required")
    return role


def record(db: Db, org: UUID, run: UUID) -> RowMapping:
    row = db.execute(text("""
        SELECT id,suite,model,status,manifest,results,scores,error_code,created_at,finished_at
        FROM app.evaluation_runs WHERE org_id=:org AND id=:id
    """), {"org": org, "id": run}).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "Evaluation run not found")
    return row


def enqueue(org: UUID, run: UUID) -> None:
    try:
        evaluate_run.apply_async(args=(str(org), str(run)), retry=False)
    except OperationalError:
        # A recorded queued run can be safely redispatched from its history.
        return


@router.post("/runs", status_code=202, dependencies=write)
def submit(organization_id: UUID, body: RunRequest, db: Db, user: User) -> dict[str, Any]:
    org = organization_id
    locked_org(db, org)
    role = scope(db, org, user)
    if body.live and (role != "admin" or not body.approve_provider_cost or not live_available()):
        raise HTTPException(403, "Live runs require operator enablement and explicit administrator cost approval")
    limit_key(f"if:evaluation:{user}", 6)
    args = {"org": org, "actor": user, "key": body.request_key, "suite": body.suite}
    inserted = db.execute(text("""
        INSERT INTO app.evaluation_runs(org_id,id,actor_id,request_key,suite,model,manifest)
        VALUES (:org,:id,:actor,:key,:suite,:model,CAST(:manifest AS jsonb))
        ON CONFLICT(org_id,actor_id,request_key) DO NOTHING RETURNING id
    """), args | {"id": uuid4(), "model": get_settings().openai_model if body.live else MODEL,
                   "manifest": json.dumps(manifest(body.live))}).scalar_one_or_none()
    run, status, mode = db.execute(text("""
        SELECT id,status,manifest->>'mode' FROM app.evaluation_runs WHERE (org_id,actor_id,request_key)=(:org,:actor,:key)
    """), args).one()
    if body.live != (mode == "live_provider"):
        raise HTTPException(409, "The request key belongs to another evaluation mode")
    if inserted:
        audit(db, "evaluation.requested", org=org, resource=run)
    db.commit()
    if status == "queued":
        enqueue(org, run)
    return {"id": run, "status": status}


@router.get("/runs")
def history(organization_id: UUID, db: Db, user: User, limit: int = Query(30, ge=1, le=100)) -> dict[str, Any]:
    scope(db, organization_id, user)
    args = {"org": organization_id, "limit": limit, "suite": SUITE}
    runs = db.execute(text("""
        SELECT id,suite,model,status,manifest,scores,error_code,created_at,finished_at,
               status='queued' OR (status='running' AND lease_until<=clock_timestamp()
                 AND manifest->>'mode'<>'live_provider') AS recoverable
        FROM app.evaluation_runs WHERE org_id=:org ORDER BY created_at DESC,id DESC LIMIT :limit
    """), args).mappings()
    baseline = db.execute(text("""
        SELECT run_id FROM app.evaluation_baselines WHERE org_id=:org AND suite=:suite
    """), args).scalar_one_or_none()
    return {"runs": [dict(row) for row in runs], "baseline_id": baseline}


@router.get("/runs/{run_id}")
def detail(organization_id: UUID, run_id: UUID, db: Db, user: User) -> dict[str, Any]:
    scope(db, organization_id, user)
    return dict(record(db, organization_id, run_id))


@router.post("/runs/{run_id}/retry", status_code=202, dependencies=write)
def retry(organization_id: UUID, run_id: UUID, db: Db, user: User) -> dict[str, UUID]:
    org = organization_id
    locked_org(db, org)
    scope(db, org, user)
    limit_key(f"if:evaluation-retry:{user}", 6)
    record(db, org, run_id)
    updated = db.execute(text("""
        UPDATE app.evaluation_runs SET status='queued',attempts=0,error_code=NULL,finished_at=NULL,
            lease_until=NULL,lease_token=NULL WHERE org_id=:org AND id=:id
          AND (status IN ('queued','failed') OR (status='running' AND lease_until<=clock_timestamp()))
          AND actor_id=:actor AND (manifest->>'mode'<>'live_provider' OR (status='queued' AND attempts=0)) RETURNING id
    """), {"org": org, "id": run_id, "actor": user}).scalar_one_or_none()
    if updated is None:
        raise HTTPException(409, "The run cannot be retried by this user")
    audit(db, "evaluation.redispatched", org=org, resource=run_id)
    db.commit()
    enqueue(org, run_id)
    return {"id": run_id}


@router.put("/runs/{run_id}/baseline", status_code=204, dependencies=write)
def pin(organization_id: UUID, run_id: UUID, db: Db, user: User) -> None:
    locked_org(db, organization_id)
    scope(db, organization_id, user, admin=True)
    row = record(db, organization_id, run_id)
    if row["status"] != "completed":
        raise HTTPException(409, "Only completed runs can be pinned")
    db.execute(text("""
        INSERT INTO app.evaluation_baselines VALUES (:org,:suite,:id)
        ON CONFLICT(org_id,suite) DO UPDATE SET run_id=excluded.run_id
    """), {"org": organization_id, "suite": row["suite"], "id": run_id})
    audit(db, "evaluation.baseline_pinned", org=organization_id, resource=run_id)


def comparison(current: RowMapping, prior: RowMapping) -> dict[str, Any]:
    if (current["status"] != "completed" or prior["status"] != "completed"
            or current["manifest"]["fixture_hash"] != prior["manifest"]["fixture_hash"]):
        raise HTTPException(409, "Comparison requires completed runs with matching fixtures")
    latest = {case["id"]: case["passed"] for case in current["results"]}
    baseline = {case["id"]: case["passed"] for case in prior["results"]}
    common = sorted(latest.keys() & baseline.keys())
    return {"paired": len(common), "regressions": [key for key in common if baseline[key] and not latest[key]],
            "improvements": [key for key in common if latest[key] and not baseline[key]],
            "current_model": current["model"], "baseline_model": prior["model"],
            "current_prompt": current["manifest"]["prompt_version"],
            "baseline_prompt": prior["manifest"]["prompt_version"]}


@router.get("/runs/{run_id}/compare")
def compare(organization_id: UUID, run_id: UUID, baseline_id: UUID, db: Db, user: User) -> dict[str, Any]:
    scope(db, organization_id, user)
    return comparison(record(db, organization_id, run_id), record(db, organization_id, baseline_id))


@router.get("/runs/{run_id}/export")
def export(organization_id: UUID, run_id: UUID, db: Db, user: User) -> JSONResponse:
    scope(db, organization_id, user)
    row = record(db, organization_id, run_id)
    if row["status"] != "completed":
        raise HTTPException(409, "The run is not complete")
    audit(db, "evaluation.exported", org=organization_id, resource=run_id)
    return JSONResponse(jsonable_encoder(dict(row)), headers={
        "Content-Disposition": 'attachment; filename="evaluation-run.json"', "X-Content-Type-Options": "nosniff",
    })
