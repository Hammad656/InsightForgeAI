"""Private training, history, model evaluation and inference endpoints."""
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from kombu.exceptions import OperationalError
from sqlalchemy import text

from app.api.v1.auth import Db, User, limit_key
from app.api.v1.organizations import locked_org, tenant, write
from app.audit import audit
from app.data_quality import quality
from app.forecast_data import source_versions
from app.jobs.analyses import encoded
from app.jobs.forecasts import run_forecast
from app.predictive import ForecastSpec

router = APIRouter(prefix="/forecasts")
FIELDS = "id,config,sources,quality,evidence,status,error_code,created_at,finished_at,artifact_expires_at"


class Request(ForecastSpec):
    organization_id: UUID
    request_key: UUID


def enqueue(org: UUID, run: UUID) -> None:
    try:
        run_forecast.apply_async(args=(str(org), str(run)), retry=False)
    except OperationalError:
        return


def authorize(org: UUID, db: Db, user: User, manage: bool = False) -> None:
    role = tenant(org, db, user)
    if manage and role == "viewer":
        raise HTTPException(403, "Analyst or administrator permission required")


def record(db: Db, org: UUID, run: UUID) -> dict[str, Any]:
    row = db.execute(text(f"""
        SELECT {FIELDS},result FROM app.forecast_runs WHERE org_id=:org AND id=:id
    """), {"org": org, "id": run}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Forecast not found")
    return dict(row)


@router.post("", status_code=202, dependencies=write)
def submit(body: Request, db: Db, user: User) -> dict[str, Any]:
    org = body.organization_id
    locked_org(db, org)
    authorize(org, db, user, manage=True)
    limit_key(f"if:forecast:{user}", 10)
    today = datetime.now(ZoneInfo("Asia/Karachi")).date()
    if body.as_of and body.as_of > today:
        raise HTTPException(422, "The training cutoff cannot be in the future")
    config = body.model_dump(mode="json", exclude={"organization_id", "request_key"})
    config["as_of"] = (body.as_of or today).isoformat()
    config["category_skus"] = sorted(body.category_skus)
    arg = {"org": org, "actor": user, "id": uuid4(), "key": body.request_key}
    prev = db.execute(text("""
        SELECT id,config,status FROM app.forecast_runs WHERE org_id=:org AND author_id=:actor AND request_key=:key
    """), arg).mappings().one_or_none()
    if prev:
        expected = config if body.as_of else config | {"as_of": prev["config"]["as_of"]}
        if prev["config"] != expected:
            raise HTTPException(409, "Request key already belongs to a different forecast")
        run, status = prev["id"], prev["status"]
    else:
        run, status = arg["id"], "queued"
        db.execute(text("""
            INSERT INTO app.forecast_runs(org_id,id,author_id,request_key,config,sources,quality)
            VALUES (:org,:id,:actor,:key,CAST(:config AS jsonb),CAST(:sources AS jsonb),CAST(:quality AS jsonb))
        """), arg | {"config": encoded(config), "sources": encoded(source_versions(db, org)),
                     "quality": encoded(quality(db, org))})
        audit(db, "forecast.requested", org=org, actor=user, resource=run, correlation=run)
    db.commit()
    if status == "queued":
        enqueue(org, run)
    return {"id": run, "status": status}


@router.get("")
def history(organization_id: UUID, db: Db, user: User, limit: int = Query(30, ge=1, le=50)) -> list[dict[str, Any]]:
    authorize(organization_id, db, user)
    return [dict(row) for row in db.execute(text(f"""
        SELECT {FIELDS},status='queued' OR status='running' AND lease_until<=clock_timestamp() AS recoverable
        FROM app.forecast_runs WHERE org_id=:org ORDER BY created_at DESC,id DESC LIMIT :limit
    """), {"org": organization_id, "limit": limit}).mappings()]


@router.get("/{run_id}")
def detail(organization_id: UUID, run_id: UUID, db: Db, user: User) -> dict[str, Any]:
    authorize(organization_id, db, user)
    return record(db, organization_id, run_id)


@router.get("/{run_id}/{section}")
def inference(organization_id: UUID, run_id: UUID, section: str, db: Db, user: User) -> dict[str, Any]:
    authorize(organization_id, db, user)
    result = record(db, organization_id, run_id)["result"] or {}
    if section not in {"anomalies", "stockout", "evaluation", "model"}:
        raise HTTPException(404, "Forecast section not found")
    keys = {"evaluation": ("backtest", "interval", "evaluation_start", "evaluation_end", "seasonal_naive_mae_delta"),
            "model": ("model", "model_version", "train_start", "train_end", "training_digest", "sources")}
    audit(db, "forecast.inference_viewed", org=organization_id, actor=user, resource=run_id, correlation=run_id)
    return {key: result[key] for key in keys.get(section, (section,)) if key in result}


@router.post("/{run_id}/retry", status_code=202, dependencies=write)
def retry(organization_id: UUID, run_id: UUID, db: Db, user: User) -> dict[str, UUID]:
    org = organization_id
    locked_org(db, org)
    authorize(org, db, user, manage=True)
    limit_key(f"if:forecast:{user}", 10)
    result = db.execute(text("""
        UPDATE app.forecast_runs SET status='queued', attempts=0,lease_token=NULL,lease_until=NULL,error_code=NULL
        WHERE org_id=:org AND id=:id AND author_id=:author
          AND (status IN ('failed','queued') OR status='running' AND lease_until<=clock_timestamp()) RETURNING id
    """), {"org": org, "id": run_id, "author": user}).scalar_one_or_none()
    if result is None:
        raise HTTPException(404, "Retryable forecast not found")
    audit(db, "forecast.retried", org=org, actor=user, resource=run_id, correlation=run_id)
    db.commit()
    enqueue(org, run_id)
    return {"id": run_id}
