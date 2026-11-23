"""Independent Decimal reference calculations, with no production SQL imports."""
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.eval_fixtures import DAY, PAYMENTS, SALES, STOCK

ZERO = Decimal(0)
FOUR = Decimal("0.0001")
BUCKETS = ("Current", "1\u201330", "31\u201360", "61\u201390", "91+")


def sales_value(metric: str, rows: list[tuple[Any, ...]], prior: list[tuple[Any, ...]]) -> Decimal | int | None:
    gross: Decimal = sum((Decimal(row[5]) * Decimal(row[6]) for row in rows), ZERO)
    net: Decimal = gross - sum((Decimal(row[7]) for row in rows), ZERO)
    cost = None if any(row[8] is None for row in rows) else sum(
        (Decimal(row[5]) * Decimal(row[8]) for row in rows), ZERO,
    )
    orders = len({row[0] for row in rows})
    if metric == "sales_growth":
        earlier = sales_value("net_revenue", prior, [])
        return ((net - earlier) * 100 / abs(earlier)).quantize(FOUR, rounding=ROUND_HALF_UP) if isinstance(
            earlier, Decimal,
        ) and earlier else None
    return {"gross_sales": gross, "net_revenue": net, "cogs": cost, "order_count": orders,
            "gross_profit": net - cost if cost is not None else None,
            "average_order_value": (net / orders).quantize(FOUR, rounding=ROUND_HALF_UP) if orders else None}[metric]


def sales(metric: str, options: dict[str, Any]) -> list[dict[str, Any]]:
    indexes = {"branch": 1, "sku": 2, "customer": 3, "day": 4}
    start, end = date.fromisoformat(options["start"]), date.fromisoformat(options["end"])
    prior_start = start - (end - start)
    scoped = [row for row in SALES if row[9] == "completed" and all(
        name not in options or row[index] == options[name] for name, index in indexes.items()
    )]
    visible = [row for row in scoped if start <= date.fromisoformat(row[4]) < end]
    prior = [row for row in scoped if prior_start <= date.fromisoformat(row[4]) < start]
    dimension = options.get("dimension")
    if not isinstance(dimension, str):
        return [{metric: sales_value(metric, visible, prior)}]
    groups: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for row in visible:
        key = row[indexes[dimension]]
        assert isinstance(key, str)
        groups[key].append(row)
    values = [{dimension: key, metric: sales_value(metric, rows, [])} for key, rows in groups.items()]
    return sorted(values, key=lambda row: (row[dimension],) if dimension == "day" else (
        row[metric] is None, -(row[metric] or 0), row[dimension],
    ))


def inventory(metric: str, options: dict[str, Any]) -> list[dict[str, Any]]:
    as_of = options.get("as_of", DAY)
    movements = [row for row in STOCK if row[2] <= as_of
                 and row[0] == options.get("sku", row[0]) and row[1] == options.get("branch", row[1])]
    positions: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for sku, branch, _, quantity, _ in movements:
        positions[sku, branch] += Decimal(quantity)
    return [{metric: {"stock_on_hand": sum(positions.values(), ZERO),
                       "low_stock": sum(quantity <= 5 for quantity in positions.values()),
                       "inventory_movements": sum(options.get("start", "0001-01-01") <= row[2]
                                                  < options.get("end", "9999-12-31") for row in movements)}[metric]}]


def receivables(metric: str, options: dict[str, Any]) -> list[dict[str, Any]]:
    as_of = options["as_of"]
    invoices: dict[str, tuple[Decimal, str]] = {}
    payments: dict[str, Decimal] = defaultdict(Decimal)
    for key, customer, issued, due, face, paid_on, paid in PAYMENTS:
        if issued > as_of or customer != options.get("customer", customer):
            continue
        invoices[key] = Decimal(face), due
        if paid_on is not None and paid is not None and paid_on <= as_of:
            payments[key] += Decimal(paid)
    balances = [(face - payments[key], (date.fromisoformat(as_of) - date.fromisoformat(due)).days, payments[key])
                for key, (face, due) in invoices.items() if face > payments[key]]
    if metric == "ageing":
        grouped: dict[str, Decimal] = defaultdict(Decimal)
        for balance, days, _ in balances:
            index = sum(days > threshold for threshold in (0, 30, 60, 90))
            grouped[BUCKETS[index]] += balance
        return [{"bucket": bucket, "balance": grouped[bucket]} for bucket in BUCKETS]
    return [{metric: {"outstanding": sum((item[0] for item in balances), ZERO),
                       "overdue_amount": sum((item[0] for item in balances if item[1] > 0), ZERO),
                       "overdue_invoices": sum(item[1] > 0 for item in balances),
                       "partial_invoices": sum(item[2] > 0 for item in balances)}[metric]}]


REFERENCES = {"sales": sales, "inventory": inventory, "receivables": receivables}
