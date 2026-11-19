"""Authenticated deterministic analytics and approved metric definitions."""
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import analytics
from app.api.v1.auth import Db
from app.api.v1.organizations import Tenant
from app.semantic import catalog

router = APIRouter(prefix="/organizations/{org_id}")
REPORTS = {"sales": analytics.sales, "inventory": analytics.inventory, "receivables": analytics.receivables}
SOURCE_DOMAINS = {"sales": "sales", "inventory": "inventory", "receivables": "payments"}


class Filters(BaseModel):
    start: date | None = None
    end: date | None = None
    as_of: date | None = None
    branch: str | None = Field(default=None, min_length=1, max_length=120)
    sku: str | None = Field(default=None, min_length=1, max_length=120)
    customer: str | None = Field(default=None, min_length=1, max_length=120)
    threshold: Decimal = Field(default=Decimal(5), ge=0, max_digits=16, decimal_places=4)


def resolve_period(filters: Filters, today: date) -> dict[str, Any]:
    start = filters.start or today.replace(day=1)
    end = filters.end
    if end is None:
        if start.year == 9999 and start.month == 12:
            raise HTTPException(422, "Provide an end date within the supported range")
        next_month = start.month % 12 + 1
        end = date(start.year + (next_month == 1), next_month, 1)
    days = (end - start).days
    if not 1 <= days <= 366 or start.toordinal() <= days:
        raise HTTPException(422, "Select an end-exclusive period of 1 to 366 days with a valid preceding period")
    return {
        **filters.model_dump(), "start": start, "end": end, "prior_start": start - timedelta(days=days),
        "as_of": filters.as_of or min(today, end - timedelta(days=1)),
    }


@router.get("/metrics")
def metrics(role: Tenant) -> list[dict[str, object]]:
    return catalog()


@router.get("/analytics/{report}")
def report(
    org_id: UUID, report: Literal["overview", "sales", "inventory", "receivables"], db: Db, role: Tenant,
    filters: Annotated[Filters, Query()],
) -> JSONResponse:
    if any("\x00" in value for value in (filters.branch, filters.sku, filters.customer) if value):
        raise HTTPException(422, "Invalid dimension filter")
    today = analytics.rows(db, "SELECT (now() AT TIME ZONE 'Asia/Karachi')::date AS today", {})[0]["today"]
    params = resolve_period(filters, today)
    sources = {
        row["domain"]: row for row in analytics.rows(db, """
            SELECT d.domain, v.id AS version, v.published_at
            FROM app.datasets d JOIN biz.dataset_versions v
              ON (v.org_id,v.domain,v.id)=(d.org_id,d.domain,d.active_version)
            WHERE d.org_id=:org
        """, {"org": org_id})
    }
    selected = REPORTS if report == "overview" else {report: REPORTS[report]}
    results: dict[str, dict[str, Any]] = {}
    for domain, compute in selected.items():
        source = sources.get(SOURCE_DOMAINS[domain])
        results[domain] = {"source": source, "metrics": None} if source is None else {
            "source": source,
            **compute(db, {**params, "org": org_id, "version": source["version"]}, report != "overview"),
        }
    return JSONResponse(jsonable_encoder({
        "currency": "PKR", "timezone": "Asia/Karachi", "semantic_version": 1,
        "filters": params, "reports": results,
    }, custom_encoder={Decimal: lambda value: format(value, "f")}))
