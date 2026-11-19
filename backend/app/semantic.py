"""Versioned expressions for the M2 snapshot contract."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    metric_id: str
    label: str
    definition: str
    sql: str | None
    unit: str


GROSS = "COALESCE(sum(quantity * unit_price), 0)"
NET = "COALESCE(sum(quantity * unit_price - discount), 0)"
COST = "CASE WHEN count(*) = count(unit_cost) THEN COALESCE(sum(quantity * unit_cost), 0) END"
ORDERS = "count(DISTINCT order_id)"
SALES = (
    Metric("gross_sales", "Gross sales", "Completed line quantity times unit price.", GROSS, "PKR"),
    Metric("net_revenue", "Net revenue", "Gross less line discounts. Refunds and taxes are outside the source contract.", NET, "PKR"),
    Metric("cogs", "COGS", "Quantity times historical line cost. Missing cost suppresses the total.", COST, "PKR"),
    Metric("gross_profit", "Gross profit", "Net revenue less historical COGS.", f"{NET} - ({COST})", "PKR"),
    Metric("order_count", "Orders", "Distinct completed orders in the selected lines.", ORDERS, "count"),
    Metric("average_order_value", "Average order value", "Net revenue divided by eligible orders.", f"round({NET} / NULLIF({ORDERS}, 0), 4)", "PKR"),
)
GROWTH = "round(100 * (current_net - prior_net) / NULLIF(abs(prior_net), 0), 4)"
STOCK = "sum(quantity)"
BALANCE = "invoice_amount - paid"
INVENTORY = (
    Metric("stock_on_hand", "Stock on hand", "Signed movements through the as-of date. Opening balances must be included as movements.", STOCK, "quantity"),
    Metric("inventory_movements", "Inventory movements", "Count of movement records in the selected period.", "count(*)", "count"),
    Metric("available_stock", "Available stock", "Stock on hand less reservations. The current source has no reservations.", None, "quantity"),
    Metric("low_stock", "Low-stock positions", "Observed SKU and branch positions at or below the selected threshold.", "count(*) FILTER (WHERE stock_on_hand <= :threshold)", "count"),
)
RECEIVABLES = (
    Metric("outstanding", "Outstanding balance", "Invoice face value less payments through the as-of date.", "COALESCE(sum(balance), 0)", "PKR"),
    Metric("overdue_amount", "Overdue amount", "Positive balance on invoices due before the as-of date.", "COALESCE(sum(balance) FILTER (WHERE days_overdue > 0), 0)", "PKR"),
    Metric("overdue_invoices", "Overdue invoices", "Open invoices due before the as-of date.", "count(*) FILTER (WHERE days_overdue > 0)", "count"),
    Metric("partial_invoices", "Partially paid invoices", "Open invoices with a positive payment total.", "count(*) FILTER (WHERE paid > 0)", "count"),
    Metric("ageing", "Receivables ageing", "Open balance grouped into current, 1–30, 31–60, 61–90 and 91+ days past due.", "sum(balance)", "PKR"),
)


def catalog() -> list[dict[str, object]]:
    groups = (
        ("sales", (*SALES, Metric("sales_growth", "Sales growth", "Net revenue change against the preceding equal-length period. Zero baseline is undefined.", GROWTH, "percent")),
         ["day", "branch", "sku", "customer"], ["start", "end", "branch", "sku", "customer"], "Completed sales only"),
        ("inventory", INVENTORY, ["sku", "branch", "movement_type"], ["start", "end", "as_of", "sku", "branch", "threshold"], "Low-stock threshold defaults to 5"),
        ("receivables", RECEIVABLES, ["invoice", "customer", "ageing_bucket"], ["as_of", "customer"], "Invoices represented in the payments snapshot"),
    )
    return [
        {**vars(metric), "version": 1, "domain": domain, "dimensions": dimensions, "filters": filters,
         "defaults": defaults, "owner": "InsightForge product", "timezone": "Asia/Karachi",
         "currency_policy": "PKR only" if metric.unit == "PKR" or domain == "sales" else None,
         "required_tables": ["app.datasets", "biz.dataset_versions", "biz.dataset_rows"]}
        for domain, metrics, dimensions, filters, defaults in groups for metric in metrics
    ]
