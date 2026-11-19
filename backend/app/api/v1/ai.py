"""Authenticated, private natural-language query execution and history."""
import json
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text

from app.ai_analytics import analyze
from app.ai_provider import ProviderUnavailable, get_provider
from app.api.v1.auth import Db, User, limit_key
from app.api.v1.organizations import tenant, write
from app.audit import audit
from app.sql_policy import QueryRejected

router = APIRouter(prefix="/ai/queries")


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    organization_id: UUID
    question: str = Field(min_length=3, max_length=500)
    dataset_ids: list[UUID] = Field(default_factory=list, max_length=3)
    business_time_zone: Literal["Asia/Karachi"] = "Asia/Karachi"
    requested_visualization: Literal["auto", "bar", "table"] = "auto"


@router.post("", dependencies=write)
def submit(body: Question, db: Db, user: User) -> dict[str, Any]:
    tenant(body.organization_id, db, user)
    if "\x00" in body.question:
        raise HTTPException(422, "Question contains unsupported characters")
    limit_key(f"if:ai:{user}", 10)
    query_id = uuid4()
    try:
        result = analyze(get_provider(), body.organization_id, body.question, body.dataset_ids, body.requested_visualization)
    except (QueryRejected, ValidationError, HTTPException):
        result = {"status": "query_rejected", "answer": "The requested analysis is outside the reporting policy."}
    except ProviderUnavailable:
        result = {"status": "execution_error", "answer": "AI analytics is currently unavailable."}
    tenant(body.organization_id, db, user)
    response: dict[str, Any] = jsonable_encoder(
        {"query_id": query_id, **result}, custom_encoder={Decimal: lambda value: format(value, "f")},
    )
    db.execute(text("""
        INSERT INTO app.ai_queries (org_id,author_id,id,question,result)
        VALUES (:org,:user,:id,:question,CAST(:result AS jsonb))
    """), {"org": body.organization_id, "user": user, "id": query_id,
           "question": body.question, "result": json.dumps(response)})
    audit(db, "ai.query_requested", org=body.organization_id, actor=user, resource=query_id)
    return response


@router.get("")
def history(organization_id: UUID, db: Db, user: User, limit: int = Query(default=20, ge=1, le=50)) -> list[dict[str, Any]]:
    tenant(organization_id, db, user)
    return [dict(row) for row in db.execute(text("""
        SELECT id AS query_id, question, created_at, result->>'status' AS status
        FROM app.ai_queries WHERE org_id=:org AND author_id=:user
        ORDER BY created_at DESC,id DESC LIMIT :limit
    """), {"org": organization_id, "user": user, "limit": limit}).mappings()]


@router.get("/{query_id}")
def retrieve(query_id: UUID, organization_id: UUID, db: Db, user: User) -> dict[str, Any]:
    tenant(organization_id, db, user)
    result = db.execute(text("""
        SELECT result,question FROM app.ai_queries WHERE org_id=:org AND author_id=:user AND id=:id
    """), {"org": organization_id, "user": user, "id": query_id}).one_or_none()
    if result is None:
        raise HTTPException(404, "Query not found")
    return {**result.result, "question": result.question}
