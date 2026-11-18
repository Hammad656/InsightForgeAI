"""Opaque, rotating browser sessions and identity endpoints."""
import hashlib
import hmac
import secrets
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Connection, text

from app.core.config import get_settings
from app.core.tenant import set_user, transaction

Db = Annotated[Connection, Depends(transaction, scope="function")]
router = APIRouter(prefix="/auth")
ACCESS = "if_access"
REFRESH = "if_refresh"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                          n=32768, r=8, p=1, maxmem=64 * 1024**2).hex()


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str) -> str:
        email = email.strip().lower()
        local, separator, host = email.partition("@")
        if not separator or not local or "." not in host or "@" in host or any(c.isspace() or ord(c) < 32 for c in email):
            raise ValueError("Enter a valid email address")
        return email


def csrf(request: Request) -> None:
    if request.headers.get("origin") not in get_settings().cors_origins or request.headers.get("x-csrf") != "1":
        raise HTTPException(403, "Request origin could not be verified")


def rate_limit(request: Request) -> None:
    address = request.client.host if request.client else "unknown"
    limit_key(f"if:auth:{request.url.path}:{digest(address)}", 20)


def limit_key(key: str, limit: int) -> None:
    try:
        with Redis.from_url(get_settings().redis_url, socket_timeout=2) as redis:
            count = redis.eval(
                "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
                1, key,
            )
    except RedisError as exc:
        raise HTTPException(503, "Authentication temporarily unavailable") from exc
    if isinstance(count, int) and count > limit:
        raise HTTPException(429, "Too many requests", headers={"Retry-After": "60"})


def issue(db: Connection, response: Response, user_id: UUID, session_id: UUID | None = None) -> None:
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    values = {"id": session_id or uuid4(), "uid": user_id, "access": digest(access), "refresh": digest(refresh)}
    if session_id:
        db.execute(text("""
            UPDATE app.sessions SET access_hash=:access, refresh_hash=:refresh,
                access_until=least(expires_at,now()+interval '15 minutes') WHERE id=:id
        """), values)
    else:
        db.execute(text("""
            INSERT INTO app.sessions (id,user_id,access_hash,refresh_hash,access_until,expires_at)
            VALUES (:id,:uid,:access,:refresh,now()+interval '15 minutes',now()+interval '7 days')
        """), values)
    for key, value, age in ((ACCESS, access, 900), (REFRESH, refresh, 604800)):
        response.set_cookie(key, value, max_age=age, secure=get_settings().cookie_secure,
                            httponly=True, samesite="lax", path="/api/v1")


def current_user(request: Request, db: Db) -> UUID:
    token = request.cookies.get(ACCESS, "")
    user_id = db.execute(text("""
        SELECT user_id FROM app.sessions WHERE access_hash=:token
            AND revoked_at IS NULL AND access_until > now() AND expires_at > now()
    """), {"token": digest(token)}).scalar_one_or_none()
    if user_id is None:
        raise HTTPException(401, "Sign in required")
    set_user(db, user_id)
    return cast(UUID, user_id)


User = Annotated[UUID, Depends(current_user)]
guards = [Depends(csrf), Depends(rate_limit)]


@router.post("/register", status_code=201, dependencies=guards)
def register(body: Credentials, db: Db, response: Response) -> dict[str, str]:
    uid, salt = uuid4(), secrets.token_hex(16)
    inserted = db.execute(text("""
        INSERT INTO app.users VALUES (:uid,:email,:password) ON CONFLICT (email) DO NOTHING RETURNING id
    """), {"uid": uid, "email": body.email, "password": f"{salt}:{password_hash(body.password, salt)}"}).scalar()
    if inserted is None:
        raise HTTPException(409, "Account could not be registered")
    issue(db, response, uid)
    return {"email": body.email}


@router.post("/login", dependencies=guards)
def login(body: Credentials, db: Db, response: Response) -> dict[str, str]:
    limit_key(f"if:auth:account:{digest(body.email)}", 12)
    row = db.execute(text("SELECT id,password_hash FROM app.users WHERE email=:email"),
                     {"email": body.email}).one_or_none()
    salt, expected = row.password_hash.split(":") if row else ("0" * 32, "0" * 128)
    valid = hmac.compare_digest(password_hash(body.password, salt), expected)
    if row is None or not valid:
        raise HTTPException(401, "Invalid credentials")
    issue(db, response, row.id)
    return {"email": body.email}


@router.post("/refresh", status_code=204, dependencies=guards)
def refresh(request: Request, db: Db, response: Response) -> None:
    row = db.execute(text("""
        SELECT id,user_id FROM app.sessions WHERE refresh_hash=:token
            AND revoked_at IS NULL AND expires_at > now() FOR UPDATE
    """), {"token": digest(request.cookies.get(REFRESH, ""))}).one_or_none()
    if row is None:
        raise HTTPException(401, "Session expired")
    issue(db, response, row.user_id, row.id)


@router.post("/logout", status_code=204, dependencies=[Depends(csrf)])
def logout(request: Request, db: Db, response: Response) -> None:
    db.execute(text("""
        UPDATE app.sessions SET revoked_at=now()
        WHERE revoked_at IS NULL AND (refresh_hash=:refresh OR access_hash=:access)
    """), {"refresh": digest(request.cookies.get(REFRESH, "")), "access": digest(request.cookies.get(ACCESS, ""))})
    for key in (ACCESS, REFRESH):
        response.delete_cookie(key, path="/api/v1", secure=get_settings().cookie_secure, httponly=True)


@router.get("/me")
def me(db: Db, user: User) -> dict[str, Any]:
    email = db.execute(text("SELECT email FROM app.users WHERE id=:uid"), {"uid": user}).scalar_one()
    organizations = db.execute(text("""
        SELECT o.id,o.name,m.role FROM app.memberships m JOIN app.organizations o ON o.id=m.org_id
        WHERE m.user_id=:uid ORDER BY o.name,o.id
    """), {"uid": user}).mappings()
    return {"id": user, "email": email, "organizations": [dict(row) for row in organizations]}
