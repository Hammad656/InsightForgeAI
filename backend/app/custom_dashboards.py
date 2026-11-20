"""Validate and compute dashboard widgets using the approved metric catalog."""
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai_analytics import METRICS, Intent, ReportingReader, report_result, reporting_transaction
from app.api.v1.analytics import SOURCE_DOMAINS, Filters, resolve_period
from app.semantic import SALES


class WidgetFilters(Filters):
    model_config = ConfigDict(extra="forbid")


class Widget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: Literal["kpi", "chart", "table"]
    metric: str = Field(max_length=60)
    dimension: Literal["day", "branch", "sku", "customer", "ageing_bucket"] | None = None
    filters: WidgetFilters = Field(default_factory=WidgetFilters)
    dataset_version: UUID | None = None
    width: Literal[1, 2] = 1
    height: Literal[1, 2] = 1

    @model_validator(mode="after")
    def approved(self) -> "Widget":
        metric = METRICS.get(self.metric)
        if not metric or metric["sql"] is None:
            raise ValueError("Select an available approved metric")
        dimension = self.dimension
        if (self.kind == "kpi" and dimension) or (self.kind == "chart" and not dimension):
            raise ValueError("This widget type is incompatible with the dimension")
        group_supported = (
            self.metric in {item.metric_id for item in SALES} and dimension in {"day", "branch", "sku", "customer"}
            or self.metric == "stock_on_hand" and dimension in {"sku", "branch"}
            or self.metric == "ageing" and dimension == "ageing_bucket"
        )
        if (dimension and not group_supported) or (self.metric == "ageing" and not dimension):
            raise ValueError("This metric and dimension are incompatible")
        allowed_filters = metric["filters"]
        assert isinstance(allowed_filters, list)
        for name, value in self.filters.model_dump(exclude_unset=True, exclude_none=True).items():
            if name not in allowed_filters or isinstance(value, str) and "\x00" in value:
                raise ValueError("This filter is unsupported")
        if self.filters.start and self.filters.end:
            days = (self.filters.end - self.filters.start).days
            if not 1 <= days <= 366 or self.filters.start.toordinal() <= days:
                raise ValueError("Select a valid end-exclusive period of up to 366 days")
        return self


def render_widgets(
    org: UUID, widgets: list[Widget], sources: dict[tuple[str, UUID | None], dict[str, Any]], now: datetime,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    today: date = now.astimezone(ZoneInfo("Asia/Karachi")).date()
    if not sources:
        return [{"id": item.id, "status": "source_unavailable"} for item in widgets]
    with reporting_transaction(org) as db:
        for widget in widgets:
            metric = METRICS[widget.metric]
            domain = str(metric["domain"])
            source_domain = SOURCE_DOMAINS[domain]
            source = sources.get((source_domain, widget.dataset_version))
            if not source:
                result.append({"id": widget.id, "status": "source_unavailable"})
                continue
            filters = resolve_period(widget.filters, today)
            reader = ReportingReader()
            intent = Intent(kind="group" if widget.dimension else "total", metric_id=widget.metric,
                            dimension=widget.dimension, start=None, end=None, as_of=None, branch=None,
                            sku=None, customer=None, limit=25)
            columns, rows = report_result(db, reader, intent, domain, widget.metric, {
                **filters, "org": org, "domain": source_domain, "version": source["version"], "result_limit": 25,
            })
            result.append({
                "id": widget.id, "status": "ready", "metric_version": metric["version"], "unit": metric["unit"],
                "columns": columns, "rows": rows, "filters": filters, "source": source,
                "stale": now - source["published_at"] > timedelta(days=7),
            })
    return result
