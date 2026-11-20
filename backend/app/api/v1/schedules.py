"""Private executive schedule management and in-app reporting notices."""
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import RowMapping, text

from app.api.v1.auth import Db, User
from app.api.v1.organizations import locked_org, tenant, write
from app.audit import audit
from app.data_quality import quality
from app.jobs.schedules import wakeup
from app.report_schedules import ScheduleConfig, next_occurrence

router = APIRouter(prefix="/report-schedules")
notices = APIRouter(prefix="/notifications")


def scope(db: Db, org: UUID, user: UUID, mutate: bool = False) -> None:
    if mutate:
        locked_org(db, org)
    if tenant(org, db, user) == "viewer":
        raise HTTPException(403, "Analyst or administrator permission required")


def owned(db: Db, org: UUID, user: UUID, schedule: UUID) -> RowMapping:
    row = db.execute(text("""
      SELECT id,config,next_at,paused FROM app.report_schedules WHERE (org_id,owner_id,id)=(:org,:user,:id)
    """), {"org": org, "user": user, "id": schedule}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Schedule not found")
    return row


@router.get("")
def listing(organization_id: UUID, db: Db, user: User) -> list[dict[str, Any]]:
    scope(db, organization_id, user)
    return [dict(row) for row in db.execute(text("""
      SELECT id,config,next_at,paused FROM app.report_schedules WHERE org_id=:org AND owner_id=:user
      ORDER BY next_at,id LIMIT 100
    """), {"org": organization_id, "user": user}).mappings()]


@router.post("", status_code=201, dependencies=write)
def create(organization_id: UUID, body: ScheduleConfig, db: Db, user: User) -> dict[str, Any]:
    org = organization_id
    scope(db, org, user, True)
    schedule = uuid4()
    next_at = next_occurrence(body, datetime.now(timezone.utc))
    db.execute(text("""
      INSERT INTO app.report_schedules(org_id,id,owner_id,config,next_at)
      VALUES (:org,:id,:user,CAST(:config AS jsonb),:due)
    """), {"org": org, "id": schedule, "user": user, "config": body.model_dump_json(), "due": next_at})
    wakeup(db, org, schedule, next_at)
    audit(db, "schedule.created", org=org, resource=schedule)
    return dict(owned(db, org, user, schedule))


@router.put("/{schedule_id}", dependencies=write)
def update(organization_id: UUID, schedule_id: UUID, body: ScheduleConfig, db: Db, user: User) -> dict[str, Any]:
    org = organization_id
    scope(db, org, user, True)
    row = owned(db, org, user, schedule_id)
    now = datetime.now(timezone.utc)
    next_at = next_occurrence(body, now)
    db.execute(text("""
      UPDATE app.report_schedules SET config=CAST(:config AS jsonb),next_at=:due WHERE org_id=:org AND id=:id
    """), {"org": org, "id": schedule_id, "config": body.model_dump_json(), "due": next_at})
    if not row["paused"]:
        wakeup(db, org, schedule_id, now)
    audit(db, "schedule.updated", org=org, resource=schedule_id)
    return dict(owned(db, org, user, schedule_id))


class ScheduleState(BaseModel):
    paused: bool


@router.patch("/{schedule_id}", dependencies=write)
def change_state(
    organization_id: UUID, schedule_id: UUID, body: ScheduleState, db: Db, user: User,
) -> dict[str, Any]:
    org = organization_id
    scope(db, org, user, True)
    row = owned(db, org, user, schedule_id)
    if row["paused"] == body.paused:
        return dict(row)
    now = datetime.now(timezone.utc)
    next_at = row["next_at"] if body.paused else next_occurrence(ScheduleConfig.model_validate(row["config"]), now)
    db.execute(text("""
      UPDATE app.report_schedules SET paused=:paused,next_at=:due WHERE org_id=:org AND id=:id
    """), {"org": org, "id": schedule_id, "paused": body.paused, "due": next_at})
    wakeup(db, org, schedule_id, now)
    audit(db, "schedule.paused" if body.paused else "schedule.resumed", org=org, resource=schedule_id)
    return dict(owned(db, org, user, schedule_id))


@router.get("/{schedule_id}/runs")
def history(organization_id: UUID, schedule_id: UUID, db: Db, user: User) -> list[dict[str, Any]]:
    scope(db, organization_id, user)
    owned(db, organization_id, user, schedule_id)
    return [dict(row) for row in db.execute(text("""
      SELECT run.due_at,run.report_id,COALESCE(r.status,'failed') AS status,
             COALESCE(run.error_code,r.error_code) AS error_code,r.finished_at,r.expires_at
      FROM app.report_schedule_runs run LEFT JOIN app.reports r ON (r.org_id,r.id)=(run.org_id,run.report_id)
      WHERE run.org_id=:org AND run.schedule_id=:id ORDER BY run.due_at DESC LIMIT 50
    """), {"org": organization_id, "id": schedule_id}).mappings()]


@notices.get("")
def notifications(organization_id: UUID, db: Db, user: User) -> list[dict[str, Any]]:
    org = organization_id
    tenant(org, db, user)
    entries = [dict(row) for row in db.execute(text("""
      SELECT r.id,r.status,r.finished_at,r.error_code,'report' AS kind
      FROM app.reports r WHERE org_id=:org AND created_by=:user AND status IN ('completed','failed')
      UNION ALL
      SELECT s.id,'failed',run.due_at,run.error_code,'schedule'
      FROM app.report_schedule_runs run JOIN app.report_schedules s ON (s.org_id,s.id)=(run.org_id,run.schedule_id)
      WHERE s.org_id=:org AND s.owner_id=:user AND run.error_code IS NOT NULL
      ORDER BY finished_at DESC LIMIT 20
    """), {"org": org, "user": user}).mappings()]
    stale = db.execute(text("""
      SELECT d.domain,v.published_at FROM app.datasets d JOIN biz.dataset_versions v
        ON (v.org_id,v.domain,v.id)=(d.org_id,d.domain,d.active_version)
      WHERE d.org_id=:org AND v.published_at<:cutoff ORDER BY d.domain
    """), {"org": org, "cutoff": datetime.now(timezone.utc) - timedelta(days=7)}).mappings()
    entries.extend(dict(row) | {"kind": "stale_dataset"} for row in stale)
    assessment = quality(db, org)
    if assessment and assessment["state"] in {"blocked", "warnings", "pending", "acknowledged"}:
        entries.append({"kind": "data_quality", "message": assessment["message"]})
    return entries
