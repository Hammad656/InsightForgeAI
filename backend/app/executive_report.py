"""Compute and render monthly business reports from frozen M3 source versions."""
from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Any, Literal
from uuid import UUID
from xml.sax.saxutils import escape

from pydantic import BaseModel, ConfigDict
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import Connection

from app import analytics
from app.ai_provider import ProviderUnavailable, get_provider
from app.semantic import catalog

TITLE = "Monthly Business Performance"
LIMITATIONS = {
    "sales": "Completed PKR sales only. Returns, credits, taxes and shipping are outside the sales source contract.",
    "inventory": "Opening balances must be present as movements. Reservations are unavailable, so available stock is unavailable.",
    "payments": "Balances cover invoices represented in the payments snapshot. Credits and invoices without source rows are excluded.",
}
SALES_KPIS = {"net_revenue": "Net revenue", "order_count": "Order count", "average_order_value": "Average order value"}
STOCK_KPIS = {"stock_on_hand": "Stock on hand", "low_stock": "Low-stock positions", "available_stock": "Available stock"}
PAYMENT_KPIS = {"outstanding": "Outstanding receivables", "overdue_amount": "Overdue receivables",
                "overdue_invoices": "Overdue invoices"}
NAVY = colors.HexColor("#152638")
TEAL = colors.HexColor("#136b67")
PALE = colors.HexColor("#e8f1f1")


class NarrativeFocus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus: Literal["sales", "stock", "collections", "coverage"]


def collect(db: Connection, org: UUID, filters: dict[str, Any], sources: dict[str, Any]) -> dict[str, Any]:
    params = {**filters, "org": org}
    for key in ("start", "end", "as_of", "prior_start"):
        params[key] = date.fromisoformat(filters[key])
    params["threshold"] = Decimal(filters["threshold"])
    result: dict[str, Any] = {}
    if "sales" in sources:
        sales_params = {**params, "version": UUID(sources["sales"]["version"])}
        current = analytics.sales(db, sales_params, False)
        previous = analytics.sales(db, {
            **sales_params, "start": params["prior_start"], "end": params["start"],
        }, False)
        result.update(sales=current["metrics"], previous=previous["metrics"], trend=current["trend"],
                      products=analytics.sales_rankings(db, sales_params, "sku"),
                      branches=analytics.sales_rankings(db, sales_params, "branch"))
    if "inventory" in sources:
        position = analytics.inventory(db, {**params, "version": UUID(sources["inventory"]["version"])}, True)
        result.update(inventory=position["metrics"], positions=position["positions"])
    if "payments" in sources:
        balances = analytics.receivables(db, {**params, "version": UUID(sources["payments"]["version"])}, False)
        result.update(payments=balances["metrics"], ageing=balances["ageing"])
    return result


def display(value: Any, money: bool = False) -> str:
    if value is None:
        return "Unavailable"
    formatted = format(value, ",f") if isinstance(value, Decimal) else f"{value:,}"
    if money:
        whole, _, fraction = formatted.partition(".")
        return f"PKR {whole}.{fraction.rstrip('0').ljust(2, '0')}"
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def narrative(data: dict[str, Any]) -> tuple[list[str], str]:
    notes: dict[str, str] = {}
    sales = data.get("sales")
    stock = data.get("inventory")
    balances = data.get("payments")
    if sales:
        notes["sales"] = (
            f"Net revenue was {display(sales['net_revenue'], True)} across {display(sales['order_count'])} "
            "completed orders. Review the branch and product tables for the distribution of sales."
        )
    if stock and stock["low_stock"] > 0:
        notes["stock"] = f"Review replenishment for the {display(stock['low_stock'])} low-stock positions."
    if balances and balances["overdue_amount"] > 0:
        notes["collections"] = (
            f"Review collection priorities for {display(balances['overdue_amount'], True)} in overdue balances."
        )
    notes["coverage"] = "Review source coverage and publication dates before interpreting the report."
    focus = next(iter(notes))
    mode = "Deterministic"
    try:
        selected, _ = get_provider().structured(
            NarrativeFocus, "Select one executive focus from the supplied options. Return only the focus.",
            {"available_focus": list(notes)},
        )
        if selected.focus in notes:
            focus, mode = selected.focus, "AI-assisted focus"
    except ProviderUnavailable:
        pass
    return [notes[focus], *(note for key, note in notes.items() if key != focus)], mode


def render(
    report_id: UUID, organization: str, filters: dict[str, Any], sources: dict[str, Any],
    data: dict[str, Any], query_id: UUID | None = None,
) -> bytes:
    stream = BytesIO()
    title = filters.get("report_title", TITLE)
    doc = SimpleDocTemplate(stream, pagesize=A4, title=title, author="InsightForge AI",
                            rightMargin=42, leftMargin=42, topMargin=54, bottomMargin=47)
    styles = getSampleStyleSheet()
    for style in ("Title", "Heading1", "Heading2"):
        styles[style].textColor = NAVY
    flow: list[Any] = []

    def paragraph(value: object, style: str = "BodyText") -> Paragraph:
        return Paragraph(escape(str(value)), styles[style])

    def add(value: object, style: str = "BodyText") -> None:
        flow.extend((paragraph(value, style), Spacer(1, 7)))

    def table(headers: list[str], records: list[list[str]], widths: list[int]) -> None:
        rows = [[paragraph(cell) for cell in row] for row in [headers, *records]]
        component = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1)
        component.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PALE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        flow.extend((component, Spacer(1, 9)))

    def page(canvas: Canvas, _document: SimpleDocTemplate) -> None:
        canvas.saveState()
        width, height = A4
        canvas.setFillColor(TEAL)
        canvas.rect(0, height - 7, width, 7, stroke=0, fill=1)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(42, 29, "INSIGHTFORGE AI  |  PRIVATE EXECUTIVE REPORT")
        canvas.drawRightString(width - 42, 29, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    add(title, "Title")
    add(organization, "Heading2")
    business_timezone = filters.get("business_timezone", "Asia/Karachi")
    add(f"Period: {filters['start']} to {filters['end']} (end exclusive) | As of {filters['as_of']} | {business_timezone}")
    add("Executive summary", "Heading1")
    if assessment := filters.get("data_quality"):
        add("Data quality: " + assessment["message"])
        add(assessment["scope"])
    observations, mode = narrative(data)
    add(f"Narrative: {mode}")
    for observation in observations:
        add(observation)
    add("Sales performance", "Heading1")
    sales, previous = data.get("sales", {}), data.get("previous", {})
    table(["Metric", "Selected period", "Prior comparable period"], [
        [label, display(sales.get(key), key != "order_count"), display(previous.get(key), key != "order_count")]
        for key, label in SALES_KPIS.items()
    ], [170, 163, 170])
    growth = sales.get("sales_growth")
    add(f"Net revenue growth: {display(growth) + '%' if growth is not None else 'Unavailable'}")
    trend = data.get("trend", [])
    if trend:
        chart = Drawing(500, 108)
        chart.add(Line(10, 20, 493, 20, strokeColor=NAVY))
        maximum = max(Decimal(1), *(row["net_revenue"] for row in trend))
        spacing = 480 / len(trend)
        for index, row in enumerate(trend):
            height = float(row["net_revenue"] / maximum) * 68
            chart.add(Rect(12 + index * spacing, 21, min(25, spacing * .72), height, fillColor=TEAL, strokeWidth=0))
        chart.add(String(11, 4, str(trend[0]["day"]), fontSize=8, fillColor=NAVY))
        chart.add(String(492, 4, str(trend[-1]["day"]), textAnchor="end", fontSize=8, fillColor=NAVY))
        add("Daily net revenue (PKR)", "Heading2")
        flow.append(chart)
    for heading, key in (("Top products by net revenue", "products"), ("Branch performance", "branches")):
        add(heading, "Heading2")
        items = data.get(key, [])
        if items:
            table(["Identifier", "Net revenue", "Orders", "Average order value"], [
                [row["name"], display(row["net_revenue"], True), display(row["order_count"]),
                 display(row["average_order_value"], True)] for row in items
            ], [130, 145, 65, 163])
        else:
            add("No matching sales records." if "sales" in data else "Unavailable")
    add("Inventory and receivables", "Heading1")
    table(["Metric", "As-of value"], [
        [label, display(data.get(domain, {}).get(key), domain == "payments" and key != "overdue_invoices")]
        for domain, keys in (("inventory", STOCK_KPIS), ("payments", PAYMENT_KPIS))
        for key, label in keys.items()
    ], [260, 243])
    low = [row for row in data.get("positions", []) if row["low_stock"]][:10]
    if low:
        add("Lowest-stock positions", "Heading2")
        table(["SKU", "Branch", "Units on hand"], [
            [row["sku"], row["branch"], display(row["stock_on_hand"])] for row in low
        ], [175, 175, 153])
    if data.get("ageing"):
        add("Receivables ageing", "Heading2")
        table(["Days overdue", "Open balance"], [[row["bucket"], display(row["balance"], True)]
              for row in data["ageing"]], [260, 243])
    flow.append(PageBreak())
    add("Evidence appendix", "Heading1")
    add(f"Report ID: {report_id} | Related Ask AI query: {query_id or 'None'}")
    if assessment:
        add(f"Reconciliation run: {assessment['id']} | As of {assessment['as_of']}")
    add(f"Prior period: {filters['prior_start']} to {filters['start']} (end exclusive)")
    for field, applies in (("branch", "sales and inventory"), ("sku", "sales and inventory"),
                           ("customer", "sales and receivables"), ("threshold", "inventory")):
        value = filters.get(field)
        add(f"{field.title()} ({applies}): {value if value is not None else 'All'}")
    table(["Source", "Version", "Published (UTC)"], [
        [domain, source["version"], source["published_at"]] if (source := sources.get(domain))
        else [domain, "Unavailable", "No published snapshot"]
        for domain in LIMITATIONS
    ], [85, 218, 200])
    add("Metric versions and definitions", "Heading2")
    included = {*SALES_KPIS, *STOCK_KPIS, *PAYMENT_KPIS, "ageing", "sales_growth"}
    table(["Metric", "Definition"], [
        [f"{entry['metric_id']} (v{entry['version']})", str(entry["definition"])]
        for entry in catalog() if entry["metric_id"] in included
    ], [175, 328])
    add("Limitations", "Heading2")
    for limitation in LIMITATIONS.values():
        add(limitation)
    doc.build(flow, onFirstPage=page, onLaterPages=page)
    return stream.getvalue()
