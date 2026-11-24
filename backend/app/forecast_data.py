"""Immutable snapshot collection, data quality evidence and M6B inference lookup."""
import hashlib
import json
from datetime import date
from statistics import fmean
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.analytics import INVENTORY_ROWS, ROWS, SALES_ROWS
from app.core.database import get_engine
from app.core.tenant import set_org
from app.data_quality import quality
from app.predictive import VERSION, WEEK, ForecastSpec, aligned, anomalies, fit, monday, stockout


def source_versions(db: Connection, org: UUID) -> dict[str, Any]:
    return {row["domain"]: {"version": str(row["version"]), "published_at": row["published_at"].isoformat()}
            for row in db.execute(text("""
      SELECT d.domain,v.id AS version,v.published_at
      FROM app.datasets d JOIN biz.dataset_versions v
        ON (v.org_id,v.domain,v.id)=(d.org_id,d.domain,d.active_version) WHERE d.org_id=:org
    """), {"org": org}).mappings()}


def calculate(db: Connection, org: UUID, spec: ForecastSpec, end: date,
              sources: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    limits = ["Weeks start Monday in Asia/Karachi. Dates refer to completed business weeks.",
              "Completed sales measure observed purchases, which may differ from unmet demand.",
              "Return-rate anomalies are unavailable because there is no governed return series."]
    if spec.category:
        limits.append("Category membership is the submitted list of SKUs.")
    limits.append("Within observed organization weeks, missing scoped sales are zero only with feed completeness attested."
                  if spec.complete_sales_feed else "Unobserved scoped weeks are missing.")
    if "sales" not in sources:
        return {"status": "insufficient_history", "limits": limits, "reason": "sales_snapshot_missing"}, None
    args = {"org": org, "end": end, "start": end - 208 * WEEK, "branch": None, "sku": None,
            "customer": None, "category": spec.category_skus, "requested_branch": spec.branch,
            "requested_sku": spec.sku, "version": UUID(sources["sales"]["version"])}
    gathered = db.execute(text(SALES_ROWS + """
      SELECT date_trunc('week',day)::date AS week,
        count(*) FILTER (WHERE (CAST(:requested_branch AS text) IS NULL OR branch=:requested_branch)
          AND (CAST(:requested_sku AS text) IS NULL OR sku=:requested_sku)
          AND (cardinality(CAST(:category AS text[]))=0 OR sku=ANY(CAST(:category AS text[])))) AS scoped,
        sum(quantity) FILTER (WHERE (CAST(:requested_branch AS text) IS NULL OR branch=:requested_branch)
          AND (CAST(:requested_sku AS text) IS NULL OR sku=:requested_sku)
          AND (cardinality(CAST(:category AS text[]))=0 OR sku=ANY(CAST(:category AS text[])))) AS demand,
        sum(quantity*unit_price-discount) FILTER (WHERE (CAST(:requested_branch AS text) IS NULL OR branch=:requested_branch)
          AND (CAST(:requested_sku AS text) IS NULL OR sku=:requested_sku)
          AND (cardinality(CAST(:category AS text[]))=0 OR sku=ANY(CAST(:category AS text[])))) AS net_revenue
      FROM selected GROUP BY 1 ORDER BY 1
    """), args).mappings().all()
    org_weeks = {row["week"] for row in gathered}
    series = {key: {row["week"]: float(row[key]) for row in gathered if row["scoped"] > 0}
              for key in ("demand", "net_revenue")}
    days, values, missing = aligned(org_weeks, series[spec.metric], end,
                                    spec.complete_sales_feed, signed=spec.metric == "net_revenue")
    result = fit(days, values, spec)
    result.update({"limits": limits, "missing_weeks": missing, "cutoff_exclusive": end.isoformat(),
                   "anomalies": {key: anomalies(values_by_week, spec.season) for key, values_by_week in series.items()},
                   "sources": sources, "scope": {"sku": spec.sku, "branch": spec.branch, "category": spec.category}})
    current_stock = None
    if inventory := sources.get("inventory"):
        inventory_args = args | {"version": UUID(inventory["version"]), "sku": spec.sku,
                                 "branch": spec.branch, "as_of": end - WEEK / 7}
        moves = db.execute(text(INVENTORY_ROWS + """
          SELECT date_trunc('week',day)::date AS week,sum(quantity) AS value FROM movements
          WHERE day>=:start AND day<:end AND (cardinality(CAST(:category AS text[]))=0
             OR sku=ANY(CAST(:category AS text[]))) GROUP BY 1
        """), inventory_args).all()
        result["anomalies"]["inventory_movements"] = anomalies({week: float(value) for week, value in moves}, spec.season)
        if spec.sku and spec.branch:
            stock = db.execute(text(INVENTORY_ROWS + "SELECT sum(stock_on_hand) FROM positions"), inventory_args).scalar_one()
            current_stock = float(stock) if stock is not None else None
    if (payments := sources.get("payments")) and not (spec.sku or spec.branch or spec.category):
        payments_rows = db.execute(text(f"""
          WITH source AS ({ROWS}), paid AS (
            SELECT (payload->>'paid_on')::date AS day,(payload->>'amount')::numeric AS amount
            FROM source WHERE payload->>'currency'='PKR'
          )
          SELECT date_trunc('week',day)::date AS week,sum(amount) FROM paid
          WHERE day>=:start AND day<:end GROUP BY 1
        """), args | {"version": UUID(payments["version"])}).all()
        result["anomalies"]["payments"] = anomalies({week: float(value) for week, value in payments_rows}, spec.season)
    else:
        limits.append("Payment anomalies require a payment snapshot and organization-wide scope.")
    if not inventory:
        limits.append("Inventory anomalies require an inventory snapshot.")
    if result["status"] != "completed":
        return result, None
    result["stockout"] = stockout(result, spec, current_stock, end, fmean(values[-4:]) / 7)
    artifact = result.pop("artifact")
    artifact["training_digest"] = hashlib.sha256(json.dumps([day.isoformat() for day in days] + values).encode()).hexdigest()
    artifact["model_version"] = VERSION
    artifact["training_weeks"] = len(values)
    result["training_digest"] = artifact["training_digest"]
    return result, artifact


def reusable_forecast(org: UUID, sales_version: str, day: date, sku: str | None, branch: str | None,
                      horizon: int | None = None) -> dict[str, Any] | None:
    with get_engine().begin() as db:
        set_org(db, org)
        assessment = quality(db, org)
        if assessment and assessment["state"] == "blocked":
            return None
        row = db.execute(text("""
          SELECT id,result,finished_at FROM app.forecast_runs WHERE org_id=:org AND status='completed'
            AND sources->'sales'->>'version'=:version AND config->>'metric'='demand'
            AND config->>'category' IS NULL AND config->>'sku' IS NOT DISTINCT FROM :sku
            AND config->>'branch' IS NOT DISTINCT FROM :branch
            AND (CAST(:horizon AS text) IS NULL OR config->>'horizon'=:horizon)
            AND result->>'cutoff_exclusive'=:cutoff AND artifact_expires_at>clock_timestamp()
          ORDER BY finished_at DESC,id DESC LIMIT 1
        """), {"org": org, "version": sales_version, "sku": sku, "branch": branch,
               "cutoff": monday(day).isoformat(), "horizon": str(horizon) if horizon else None}).mappings().one_or_none()
        if not row:
            return None
        result = row["result"]
        points = result["forecast"]
        return {
            "status": "completed", "answer": "Estimated demand from a completed, backtested forecast.",
            "result": {"columns": ["week", "lower", "upper", "point"], "rows": points},
            "visualization": {"type": "table", "x": "week", "y": "point"},
            "evidence": {"metric_id": "demand_forecast", "metric_version": 1, "unit": "units",
                         "dataset_versions": [sales_version], "published_at": result["sources"]["sales"]["published_at"],
                         "generated_at": row["finished_at"], "timezone": "Asia/Karachi",
                         "query_hash": "sha256:" + result["training_digest"], "row_count": len(points),
                         "filters": {"sku": sku, "branch": branch, "horizon_weeks": len(points)},
                         "warnings": result["limits"] + [
                             f"Model {result['model']} ({result['model_version']}). Training {result['train_start']} to {result['train_end']}.",
                             f"Target interval coverage {result['interval']['target']:.0%}; measured on chronological holdout.",
                         ], "forecast_id": row["id"], "model": result["model_version"], "interval": result["interval"]},
        }
