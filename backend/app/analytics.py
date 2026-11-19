"""Fixed, parameterized PostgreSQL reports over immutable dataset versions."""
from collections.abc import Callable
from typing import Any, Literal

from sqlalchemy import Connection, text

from app.semantic import BALANCE, GROWTH, INVENTORY, RECEIVABLES, SALES, STOCK

ROWS = """
    SELECT payload FROM biz.dataset_rows
    WHERE org_id=:org AND version_id=:version
"""
SALES_ROWS = f"""
    WITH source AS ({ROWS}), sales AS (
        SELECT payload->>'order_id' AS order_id, payload->>'branch_id' AS branch,
               payload->>'sku' AS sku, payload->>'customer_id' AS customer,
               (payload->>'ordered_on')::date AS day,
               (payload->>'quantity')::numeric AS quantity,
               (payload->>'unit_price')::numeric AS unit_price,
               (payload->>'discount')::numeric AS discount,
               (payload->>'unit_cost')::numeric AS unit_cost
        FROM source
        WHERE payload->>'status'='completed' AND payload->>'currency'='PKR'
          AND (CAST(:branch AS text) IS NULL OR payload->>'branch_id'=:branch)
          AND (CAST(:sku AS text) IS NULL OR payload->>'sku'=:sku)
          AND (CAST(:customer AS text) IS NULL OR payload->>'customer_id'=:customer)
    ), selected AS (
        SELECT * FROM sales WHERE day>=:start AND day<:end
    )
"""
SALES_AGGREGATES = ", ".join(f"{metric.sql} AS {metric.metric_id}" for metric in SALES)
INVENTORY_ROWS = f"""
    WITH source AS ({ROWS}), movements AS (
        SELECT payload->>'sku' AS sku, payload->>'branch_id' AS branch,
               payload->>'movement_type' AS movement_type,
               (payload->>'quantity')::numeric AS quantity,
               (payload->>'movement_on')::date AS day
        FROM source
        WHERE (CAST(:sku AS text) IS NULL OR payload->>'sku'=:sku)
          AND (CAST(:branch AS text) IS NULL OR payload->>'branch_id'=:branch)
    ), positions AS (
        SELECT sku, branch, {STOCK} AS stock_on_hand
        FROM movements WHERE day<=:as_of GROUP BY sku, branch
    )
"""
RECEIVABLES_ROWS = f"""
    WITH source AS ({ROWS}), invoices AS (
        SELECT payload->>'invoice_id' AS invoice, min(payload->>'customer_id') AS customer,
               min((payload->>'due_on')::date) AS due_on,
               max((payload->>'invoice_amount')::numeric) AS invoice_amount,
               COALESCE(sum((payload->>'amount')::numeric) FILTER (
                   WHERE (payload->>'paid_on')::date<=:as_of), 0) AS paid
        FROM source
        WHERE (payload->>'invoice_on')::date<=:as_of AND payload->>'currency'='PKR'
          AND (CAST(:customer AS text) IS NULL OR payload->>'customer_id'=:customer)
        GROUP BY payload->>'invoice_id'
    ), open_invoices AS (
        SELECT *, {BALANCE} AS balance, CAST(:as_of AS date)-due_on AS days_overdue
        FROM invoices WHERE {BALANCE}>0
    ), aged AS (
        SELECT *, CASE WHEN days_overdue<=0 THEN 'Current' WHEN days_overdue<=30 THEN '1–30'
                       WHEN days_overdue<=60 THEN '31–60' WHEN days_overdue<=90 THEN '61–90'
                       ELSE '91+' END AS bucket
        FROM open_invoices
    )
"""


def rows(db: Connection, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(text(query), params).mappings()]


ReadRows = Callable[[Connection, str, dict[str, Any]], list[dict[str, Any]]]


def sales_rankings(db: Connection, params: dict[str, Any], dimension: Literal["sku", "branch"]) -> list[dict[str, Any]]:
    column = {"sku": "sku", "branch": "branch"}[dimension]
    return rows(db, SALES_ROWS + f"""
        SELECT {column} AS name, {SALES_AGGREGATES} FROM selected
        GROUP BY {column} ORDER BY net_revenue DESC, {column} LIMIT 10
    """, params)


def sales(db: Connection, params: dict[str, Any], details: bool, read: ReadRows = rows) -> dict[str, Any]:
    metrics = read(db, SALES_ROWS + f""",
        current_period AS (SELECT {SALES_AGGREGATES} FROM selected),
        prior_period AS (
            SELECT COALESCE(sum(quantity*unit_price-discount), 0) AS prior_net
            FROM sales WHERE day>=:prior_start AND day<:start
        ), comparison AS (
            SELECT current_period.*, net_revenue AS current_net, prior_net
            FROM current_period CROSS JOIN prior_period
        )
        SELECT gross_sales, net_revenue, cogs, gross_profit, order_count,
               average_order_value, prior_net AS prior_net_revenue, {GROWTH} AS sales_growth
        FROM comparison
    """, params)[0]
    result: dict[str, Any] = {
        "metrics": metrics,
        "trend": read(db, SALES_ROWS + f"""
            SELECT day, {SALES_AGGREGATES} FROM selected GROUP BY day ORDER BY day
        """, params),
    }
    if details:
        result["breakdown"] = read(db, SALES_ROWS + f"""
            SELECT branch, sku, {SALES_AGGREGATES}
            FROM selected GROUP BY branch, sku ORDER BY net_revenue DESC, branch, sku LIMIT 100
        """, params)
    return result


def inventory(db: Connection, params: dict[str, Any], details: bool, read: ReadRows = rows) -> dict[str, Any]:
    metrics = read(db, INVENTORY_ROWS + f"""
        SELECT COALESCE(sum(stock_on_hand), 0) AS stock_on_hand,
               {INVENTORY[3].sql} AS low_stock,
               (SELECT count(*) FROM movements WHERE day>=:start AND day<:end AND day<=:as_of)
                   AS inventory_movements
        FROM positions
    """, params)[0]
    metrics["available_stock"] = None
    result: dict[str, Any] = {"metrics": metrics}
    if details:
        result["positions"] = read(db, INVENTORY_ROWS + """
            SELECT *, stock_on_hand<=:threshold AS low_stock
            FROM positions ORDER BY stock_on_hand, branch, sku LIMIT 100
        """, params)
        result["movements"] = read(db, INVENTORY_ROWS + """
            SELECT movement_type, count(*) AS movement_count, sum(quantity) AS net_quantity
            FROM movements WHERE day>=:start AND day<:end AND day<=:as_of
            GROUP BY movement_type ORDER BY movement_type
        """, params)
    return result


def receivables(db: Connection, params: dict[str, Any], details: bool, read: ReadRows = rows) -> dict[str, Any]:
    expressions = ", ".join(f"{metric.sql} AS {metric.metric_id}" for metric in RECEIVABLES[:4])
    result: dict[str, Any] = {
        "metrics": read(db, RECEIVABLES_ROWS + f"SELECT {expressions} FROM aged", params)[0],
        "ageing": read(db, RECEIVABLES_ROWS + """
            SELECT bins.bucket, COALESCE(sum(aged.balance), 0) AS balance
            FROM (VALUES (0,'Current'),(1,'1–30'),(2,'31–60'),(3,'61–90'),(4,'91+')) bins(sort,bucket)
            LEFT JOIN aged ON aged.bucket=bins.bucket GROUP BY bins.sort,bins.bucket ORDER BY bins.sort
        """, params),
    }
    if details:
        result["invoices"] = read(db, RECEIVABLES_ROWS + """
            SELECT * FROM aged ORDER BY due_on, invoice LIMIT 100
        """, params)
    return result
