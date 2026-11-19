"""Organization membership and server-enforced roles."""
import secrets
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.v1.auth import Credentials, Db, User, csrf, digest
from app.audit import audit, org_context
from app.core.tenant import set_org

Role = Literal["admin", "analyst", "viewer"]
router = APIRouter(prefix="/organizations")
write = [Depends(csrf)]


class Organization(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MemberRole(BaseModel):
    role: Role


class Invitation(MemberRole):
    email: str = Field(max_length=254)


class InvitationToken(BaseModel):
    token: str = Field(min_length=32, max_length=100)


def locked_org(db: Db, org_id: UUID) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": str(org_id)})


def tenant(org_id: UUID, db: Db, user: User) -> Role:
    role = db.execute(text("SELECT role FROM app.memberships WHERE org_id=:org AND user_id=:uid"),
                      {"org": org_id, "uid": user}).scalar_one_or_none()
    if role is None:
        raise HTTPException(404, "Organization not found")
    set_org(db, org_id)
    org_context(org_id)
    return cast(Role, role)


def admin(org_id: UUID, db: Db, user: User) -> Role:
    locked_org(db, org_id)
    role = tenant(org_id, db, user)
    if role != "admin":
        raise HTTPException(403, "Administrator permission required")
    return role


Tenant = Annotated[Role, Depends(tenant)]
Admin = Annotated[Role, Depends(admin)]


@router.post("", status_code=201, dependencies=write)
def create(body: Organization, db: Db, user: User) -> dict[str, str | UUID]:
    org_id = uuid4()
    name = body.name.strip()
    if not name or "\x00" in name:
        raise HTTPException(422, "Enter an organization name")
    set_org(db, org_id)
    db.execute(text("INSERT INTO app.organizations (id,name) VALUES (:org,:name)"), {"org": org_id, "name": name})
    db.execute(text("INSERT INTO app.memberships VALUES (:org,:uid,'admin')"), {"org": org_id, "uid": user})
    audit(db, "organization.created", org=org_id, resource=org_id)
    return {"id": org_id, "name": name, "role": "admin"}


@router.get("/{org_id}/members")
def members(org_id: UUID, db: Db, role: Admin) -> list[dict[str, Any]]:
    rows = db.execute(text("""
        SELECT u.id,u.email,m.role FROM app.memberships m JOIN app.users u ON u.id=m.user_id
        WHERE m.org_id=:org ORDER BY u.email
    """), {"org": org_id}).mappings()
    return [dict(row) for row in rows]


@router.post("/{org_id}/invitations", status_code=201, dependencies=write)
def invite(org_id: UUID, body: Invitation, db: Db, role: Admin) -> dict[str, str]:
    try:
        email = Credentials.normalize_email(body.email)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    token = secrets.token_urlsafe(32)
    db.execute(text("""
        INSERT INTO app.invitations (org_id,email,role,token_hash,expires_at)
        VALUES (:org,:email,:role,:hash,now()+interval '48 hours')
        ON CONFLICT (org_id,email) DO UPDATE SET role=excluded.role,
            token_hash=excluded.token_hash,expires_at=excluded.expires_at
    """), {"org": org_id, "email": email, "role": body.role, "hash": digest(token)})
    audit(db, "membership.invited", org=org_id)
    return {"token": token}


@router.post("/{org_id}/invitations/accept", status_code=204, dependencies=write)
def accept(org_id: UUID, body: InvitationToken, db: Db, user: User) -> None:
    locked_org(db, org_id)
    set_org(db, org_id)
    role = db.execute(text("""
        DELETE FROM app.invitations WHERE org_id=:org AND token_hash=:hash AND expires_at>now()
            AND email=(SELECT email FROM app.users WHERE id=:uid) RETURNING role
    """), {"org": org_id, "hash": digest(body.token), "uid": user}).scalar_one_or_none()
    if role is None:
        raise HTTPException(404, "Invitation not found")
    db.execute(text("INSERT INTO app.memberships VALUES (:org,:uid,:role) ON CONFLICT DO NOTHING"),
               {"org": org_id, "uid": user, "role": role})
    audit(db, "membership.accepted", org=org_id, actor=user, resource=user)


@router.patch("/{org_id}/members/{member_id}", status_code=204, dependencies=write)
def update_member(org_id: UUID, member_id: UUID, db: Db, role: Admin, body: MemberRole) -> None:
    change_member(org_id, member_id, db, body.role)
    audit(db, "membership.role_changed", org=org_id, resource=member_id)


@router.delete("/{org_id}/members/{member_id}", status_code=204, dependencies=write)
def remove_member(org_id: UUID, member_id: UUID, db: Db, role: Admin) -> None:
    change_member(org_id, member_id, db, None)
    audit(db, "membership.removed", org=org_id, resource=member_id)


def change_member(org_id: UUID, member_id: UUID, db: Db, new_role: Role | None) -> None:
    params = {"org": org_id, "uid": member_id}
    previous = db.execute(text("SELECT role FROM app.memberships WHERE org_id=:org AND user_id=:uid"),
                          params).scalar_one_or_none()
    if previous is None:
        raise HTTPException(404, "Member not found")
    if previous == "admin" and new_role != "admin":
        count = db.execute(text("SELECT count(*) FROM app.memberships WHERE org_id=:org AND role='admin'"),
                           params).scalar_one()
        if count == 1:
            raise HTTPException(409, "The organization must retain an administrator")
    if new_role is None:
        db.execute(text("DELETE FROM app.memberships WHERE org_id=:org AND user_id=:uid"), params)
    else:
        db.execute(text("UPDATE app.memberships SET role=:role WHERE org_id=:org AND user_id=:uid"),
                   {**params, "role": new_role})
