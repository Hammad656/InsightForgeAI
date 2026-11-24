"""Durable, author-scoped orchestration on the existing Celery broker."""
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError

from app.ai_analytics import SOURCE_DOMAINS
from app.ai_provider import get_provider
from app.audit import audit
from app.core.database import get_engine
from app.core.tenant import set_org, set_user
from app.jobs.celery_app import celery_app
from app.orchestration import MAX_TOKENS, Interrupted, Task, coordinate, plan_question, task_result

TERMINAL = {"completed", "partial", "failed", "clarification_required", "cancelled", "timed_out"}
KEY = "org_id=:org AND author_id=:author AND id=:id"
AUTH = """
    EXISTS (SELECT 1 FROM app.memberships m WHERE m.org_id=:org AND m.user_id=:author
      AND m.role IN ('admin','analyst'))
"""


def encoded(value: Any) -> str:
    return json.dumps(jsonable_encoder(value, custom_encoder={Decimal: lambda item: format(item, "f")}))


@contextmanager
def transaction_as(params: dict[str, UUID]) -> Iterator[Connection]:
    with get_engine().begin() as db:
        set_user(db, params["author"])
        set_org(db, params["org"])
        yield db


def guard(params: dict[str, UUID]) -> int:
    with transaction_as(params) as db:
        row = db.execute(text(f"""
            SELECT status,deadline_at,clock_timestamp() AS checked_at,{AUTH} AS allowed
            FROM app.ai_analyses WHERE {KEY}
        """), params).one_or_none()
    if row is None or not row.allowed or row.status == "cancelled":
        raise Interrupted("cancelled")
    if row.status != "running":
        raise Interrupted("timed_out")
    remaining = int((row.deadline_at - row.checked_at).total_seconds() * 1000)
    if remaining <= 0:
        raise Interrupted("timed_out")
    return remaining


def claim(params: dict[str, UUID]) -> dict[str, Any] | None:
    with transaction_as(params) as db:
        row = db.execute(text(f"""
            UPDATE app.ai_analyses SET status='running',started_at=clock_timestamp(),
                deadline_at=clock_timestamp()+interval '40 seconds'
            WHERE {KEY} AND status='queued' AND created_at>clock_timestamp()-interval '2 minutes'
            RETURNING *
        """), params).mappings().one_or_none()
        if row:
            audit(db, "analysis.started", org=params["org"], actor=params["author"], resource=params["id"],
                  correlation=row["correlation_id"])
        return dict(row) if row else None


def finish(params: dict[str, UUID], job: dict[str, Any], status: str, summary: dict[str, Any]) -> None:
    with transaction_as(params) as db:
        ended = db.execute(text(f"""
            UPDATE app.ai_analyses SET status=:status,summary=CAST(:summary AS jsonb),finished_at=clock_timestamp()
            WHERE {KEY} AND status='running' AND (
                :status IN ('cancelled','timed_out') OR ({AUTH} AND deadline_at>clock_timestamp())
            ) RETURNING id
        """), {**params, "status": status, "summary": encoded(summary)}).scalar_one_or_none()
        if ended:
            audit(db, "analysis." + status, org=params["org"], actor=params["author"], resource=params["id"],
                  correlation=job["correlation_id"])


def progress(params: dict[str, UUID], job: dict[str, Any], task: Task, result: dict[str, Any]) -> None:
    guard(params)
    entry = {
        **result, "task_id": task.id, "domain": task.domain, "correlation_id": job["correlation_id"],
        "updated_at": datetime.now(timezone.utc),
    }
    evidence = result.get("evidence")
    if evidence and job["data_quality"]:
        evidence["data_quality"] = job["data_quality"]
        evidence["warnings"] = [*evidence["warnings"], job["data_quality"]["message"]]
    with transaction_as(params) as db:
        saved = db.execute(text(f"""
            UPDATE app.ai_analyses
            SET tasks=jsonb_set(tasks,ARRAY[CAST(:task AS text)],CAST(:entry AS jsonb),true)
            WHERE {KEY} AND status='running' AND {AUTH} AND deadline_at>clock_timestamp() RETURNING id
        """), {**params, "task": task.id, "entry": encoded(entry)}).scalar_one_or_none()
        if not saved:
            raise Interrupted("cancelled")
        audit(db, "analysis.task." + result["status"], org=params["org"], actor=params["author"],
              resource=params["id"], correlation=job["correlation_id"])


def summarize(results: dict[int, dict[str, Any]], quality: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    completed = [item for item in results.values() if item["status"] == "completed"]
    incomplete = len(results) - len(completed)
    status = "completed" if not incomplete else ("partial" if completed else "failed")
    recommendations = []
    if any(item.get("evidence", {}).get("metric_id") == "overdue_amount" and any(
        Decimal(str(row["overdue_amount"])) > 0 for row in item["result"]["rows"]
        if row.get("overdue_amount") is not None
    ) for item in completed):
        recommendations.append("Review the overdue balances before prioritizing collection follow-ups.")
    if any(item.get("result", {}).get("columns") == ["sku", "branch", "stock_on_hand"] for item in completed):
        recommendations.append("Review replenishment for the low-stock positions shown.")
    if quality and quality["state"] != "clear":
        recommendations.append("Review the open data-quality context before acting on the results.")
    return status, {
        "message": f"{len(completed)} of {len(results)} tasks returned verified results.",
        "findings": [item["answer"] for _, item in sorted(results.items()) if item["status"] == "completed"],
        "warnings": [item["answer"] for _, item in sorted(results.items()) if item["status"] != "completed"],
        "recommendations": recommendations,
    }


@celery_app.task(acks_late=True, ignore_result=True, reject_on_worker_lost=True, soft_time_limit=48, time_limit=55,
                 autoretry_for=(OperationalError,), max_retries=2, retry_backoff=True)
def run_analysis(org_id: str, author_id: str, analysis_id: str) -> None:
    params = {"org": UUID(org_id), "author": UUID(author_id), "id": UUID(analysis_id)}
    job = claim(params)
    if not job:
        return
    try:
        guard(params)
        plan, tokens = plan_question(get_provider(), job["question"], job["business_day"])
        guard(params)
        with transaction_as(params) as db:
            published = db.execute(text(f"""
                UPDATE app.ai_analyses SET plan=CAST(:plan AS jsonb),planner_tokens=:tokens
                WHERE {KEY} AND {AUTH} AND status='running' AND deadline_at>clock_timestamp() RETURNING id
            """), {**params, "plan": plan.model_dump_json() if 0 <= tokens <= MAX_TOKENS else None,
                   "tokens": min(MAX_TOKENS + 1, max(0, tokens))}).scalar_one_or_none()
        if not published:
            raise Interrupted("cancelled")
        if tokens < 0 or tokens > MAX_TOKENS:
            finish(params, job, "failed", {"message": "The planner exceeded its token budget."})
            return
        if plan.status != "ready":
            finish(params, job, "clarification_required" if plan.status == "clarification_required" else "failed",
                   {"message": "Specify supported metrics, exact identifiers, or a clearer reporting period."})
            return

        def execute(task: Task) -> dict[str, Any]:
            progress(params, job, task, {"status": "running", "answer": "Executing the authorized task."})
            source = job["sources"].get("sales" if task.domain == "forecast" else SOURCE_DOMAINS.get(task.domain))
            return task_result(task, params["org"], source, job["business_day"], lambda: guard(params))

        results = coordinate(plan, execute, lambda task, result: progress(params, job, task, result), lambda: guard(params))
        guard(params)
        status, summary = summarize(results, job["data_quality"])
        finish(params, job, status, summary)
    except Interrupted as exc:
        finish(params, job, exc.status, {"message": "The analysis stopped before all tasks completed."})
    except Exception:
        finish(params, job, "failed", {"message": "The analysis could not safely complete."})
