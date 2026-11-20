"""Tenant-scoped dashboard editing, sharing and fresh metric evaluation."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import RowMapping, text
from sqlalchemy.exc import OperationalError

from app.ai_analytics import METRICS
from app.ai_provider import ProviderUnavailable
from app.api.v1.analytics import SOURCE_DOMAINS
from app.api.v1.auth import Db, User, limit_key
from app.api.v1.organizations import Role, locked_org, tenant, write
from app.audit import audit
from app.custom_dashboards import Widget, render_widgets
from app.data_quality import quality
from app.sql_policy import QueryRejected

router = APIRouter(prefix="/dashboards")
VISIBLE = """(d.owner_id=:actor OR EXISTS (SELECT 1 FROM app.dashboard_shares s
             WHERE (s.org_id,s.dashboard_id,s.user_id)=(d.org_id,d.id,:actor)))"""


class Dashboard(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120, pattern=r"^[^\x00]+$")
    widgets: list[Widget] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def unique_widgets(self) -> "Dashboard":
        if len({w.id for w in self.widgets}) != len(self.widgets):
            raise ValueError("Widget identifiers must be unique")
        return self


class DashboardUpdate(Dashboard):
    revision: int = Field(ge=1)


class Share(BaseModel):
    editor: bool = False


def access(db: Db, org: UUID, actor: UUID, dashboard: UUID, role: Role, change: bool = False) -> RowMapping:
    row = db.execute(text(f"""
        SELECT d.*, (d.owner_id=:actor OR EXISTS (
          SELECT 1 FROM app.dashboard_shares s WHERE (s.org_id,s.dashboard_id,s.user_id)=(d.org_id,d.id,:actor)
          AND s.editor)) AND :writer AS editable
        FROM app.dashboards d WHERE d.org_id=:org AND d.id=:id AND {VISIBLE}
    """), {"org": org, "actor": actor, "id": dashboard, "writer": role != "viewer"}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Dashboard not found")
    if change and not row["editable"]:
        raise HTTPException(403, "Dashboard editing is not permitted")
    return row


def sources(db: Db, org: UUID, widgets: list[Widget]) -> dict[tuple[str, UUID | None], dict[str, Any]]:
    requested = {widget.dataset_version for widget in widgets if widget.dataset_version}
    entries: dict[tuple[str, UUID | None], dict[str, Any]] = {}
    for row in db.execute(text("""
        SELECT v.domain,v.id AS version,v.published_at, d.active_version=v.id AS active
        FROM app.datasets d JOIN biz.dataset_versions v ON (d.org_id,d.domain)=(v.org_id,v.domain)
        WHERE d.org_id=:org AND (d.active_version=v.id OR v.id=ANY(CAST(:versions AS uuid[])))
    """), {"org": org, "versions": list(requested)}).mappings():
        source = dict(row)
        if row["active"]:
            entries[(row["domain"], None)] = source
        entries[(row["domain"], row["version"])] = source
    for widget in widgets:
        domain = SOURCE_DOMAINS[str(METRICS[widget.metric]["domain"])]
        if widget.dataset_version and (domain, widget.dataset_version) not in entries:
            raise HTTPException(404, "Dataset version not found")
    return entries


@router.get("")
def listing(organization_id: UUID, db: Db, user: User) -> dict[str, Any]:
    tenant(organization_id, db, user)
    params = {"org": organization_id, "actor": user}
    dashboards = db.execute(text(f"""
        SELECT d.id,d.title,d.owner_id,d.revision FROM app.dashboards d
        WHERE d.org_id=:org AND {VISIBLE} ORDER BY d.title,d.id
    """), params).mappings()
    default_id = db.execute(text("""
        SELECT dashboard_id FROM app.dashboard_defaults WHERE org_id=:org AND user_id=:actor
    """), params).scalar_one_or_none()
    items = [dict(row) for row in dashboards]
    return {"dashboards": items, "default_id": default_id if any(row["id"] == default_id for row in items) else None}


@router.post("", status_code=201, dependencies=write)
def create(organization_id: UUID, body: Dashboard, db: Db, user: User) -> dict[str, Any]:
    org = organization_id
    locked_org(db, org)
    if tenant(org, db, user) == "viewer":
        raise HTTPException(403, "Analyst or administrator permission required")
    sources(db, org, body.widgets)
    dashboard = uuid4()
    db.execute(text("""
        INSERT INTO app.dashboards(org_id,id,owner_id,title,widgets)
        VALUES (:org,:id,:actor,:title,CAST(:widgets AS jsonb))
    """), {"org": org, "id": dashboard, "actor": user, "title": body.title,
           "widgets": json.dumps([w.model_dump(mode="json", exclude_unset=True) for w in body.widgets])})
    audit(db, "dashboard.created", org=org, resource=dashboard)
    return {"id": dashboard}


@router.get("/{dashboard_id}")
def detail(organization_id: UUID, dashboard_id: UUID, db: Db, user: User) -> dict[str, Any]:
    role = tenant(organization_id, db, user)
    row = access(db, organization_id, user, dashboard_id, role)
    shares = []
    if row["owner_id"] == user and role != "viewer":
        shares = [dict(item) for item in db.execute(text("""
            SELECT s.user_id,s.editor FROM app.dashboard_shares s JOIN app.memberships m USING(org_id,user_id)
            WHERE s.org_id=:org AND s.dashboard_id=:id ORDER BY s.user_id
        """), {"org": organization_id, "id": dashboard_id}).mappings()]
    return dict(row) | {"shares": shares}


@router.put("/{dashboard_id}", dependencies=write)
def update(organization_id: UUID, dashboard_id: UUID, body: DashboardUpdate, db: Db, user: User) -> dict[str, int]:
    org = organization_id
    locked_org(db, org)
    access(db, org, user, dashboard_id, tenant(org, db, user), True)
    sources(db, org, body.widgets)
    revision = db.execute(text("""
        UPDATE app.dashboards SET title=:title,widgets=CAST(:widgets AS jsonb),revision=revision+1
        WHERE org_id=:org AND id=:id AND revision=:revision RETURNING revision
    """), {"org": org, "id": dashboard_id, "title": body.title, "revision": body.revision,
           "widgets": json.dumps([w.model_dump(mode="json", exclude_unset=True) for w in body.widgets])}).scalar_one_or_none()
    if revision is None:
        raise HTTPException(409, "Dashboard changed. Reload before saving.")
    audit(db, "dashboard.updated", org=org, resource=dashboard_id)
    return {"revision": revision}


@router.delete("/{dashboard_id}", status_code=204, dependencies=write)
def delete(organization_id: UUID, dashboard_id: UUID, db: Db, user: User) -> None:
    org = organization_id
    locked_org(db, org)
    row = access(db, org, user, dashboard_id, tenant(org, db, user), True)
    if row["owner_id"] != user:
        raise HTTPException(403, "Dashboard owner required")
    db.execute(text("DELETE FROM app.dashboards WHERE org_id=:org AND id=:id"), {"org": org, "id": dashboard_id})
    audit(db, "dashboard.deleted", org=org, resource=dashboard_id)


@router.post("/{dashboard_id}/duplicate", status_code=201, dependencies=write)
def duplicate(organization_id: UUID, dashboard_id: UUID, db: Db, user: User) -> dict[str, Any]:
    locked_org(db, organization_id)
    row = access(db, organization_id, user, dashboard_id, tenant(organization_id, db, user))
    return create(organization_id, Dashboard.model_validate({
        "title": row["title"][:113] + " (copy)", "widgets": row["widgets"],
    }), db, user)


@router.put("/{dashboard_id}/default", status_code=204, dependencies=write)
def set_default(organization_id: UUID, dashboard_id: UUID, db: Db, user: User) -> None:
    locked_org(db, organization_id)
    access(db, organization_id, user, dashboard_id, tenant(organization_id, db, user))
    db.execute(text("""
        INSERT INTO app.dashboard_defaults VALUES (:org,:actor,:id)
        ON CONFLICT(org_id,user_id) DO UPDATE SET dashboard_id=excluded.dashboard_id
    """), {"org": organization_id, "actor": user, "id": dashboard_id})


@router.put("/{dashboard_id}/shares/{member_id}", status_code=204, dependencies=write)
@router.delete("/{dashboard_id}/shares/{member_id}", status_code=204, dependencies=write)
def change_share(
    organization_id: UUID, dashboard_id: UUID, member_id: UUID, db: Db, user: User, body: Share | None = None,
) -> None:
    org = organization_id
    locked_org(db, org)
    row = access(db, org, user, dashboard_id, tenant(org, db, user), True)
    if row["owner_id"] != user:
        raise HTTPException(403, "Dashboard owner required")
    params = {"org": org, "id": dashboard_id, "member": member_id}
    if body is None:
        db.execute(text("DELETE FROM app.dashboard_shares WHERE (org_id,dashboard_id,user_id)=(:org,:id,:member)"), params)
    else:
        role = db.execute(text("SELECT role FROM app.memberships WHERE org_id=:org AND user_id=:member"),
                          params).scalar_one_or_none()
        if not role or member_id == user:
            raise HTTPException(404, "Eligible member not found")
        if body.editor and role == "viewer":
            raise HTTPException(422, "Viewer members have read-only access")
        sources(db, org, [Widget.model_validate(item) for item in row["widgets"]])
        db.execute(text("""
            INSERT INTO app.dashboard_shares VALUES (:org,:id,:member,:editor)
            ON CONFLICT(org_id,dashboard_id,user_id) DO UPDATE SET editor=excluded.editor
        """), params | {"editor": body.editor})
    audit(db, "dashboard.shared" if body else "dashboard.share_revoked", org=org, resource=dashboard_id)


@router.post("/{dashboard_id}/refresh", dependencies=write)
def refresh(organization_id: UUID, dashboard_id: UUID, db: Db, user: User) -> Any:
    org = organization_id
    locked_org(db, org)
    row = access(db, org, user, dashboard_id, tenant(org, db, user))
    limit_key(f"if:dashboard-refresh:{user}", 30)
    widgets = [Widget.model_validate(w) for w in row["widgets"]]
    now = datetime.now(timezone.utc)
    snapshot = sources(db, org, widgets)
    try:
        results = render_widgets(org, widgets, snapshot, now)
    except (OperationalError, ProviderUnavailable, QueryRejected):
        raise HTTPException(503, "Dashboard refresh did not complete. Try refreshing again.") from None
    return jsonable_encoder({"refreshed_at": now, "revision": row["revision"],
                            "widgets": results,
                            "data_quality": quality(db, org)}, custom_encoder={Decimal: str})
