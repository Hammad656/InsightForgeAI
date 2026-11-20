"""Pure normalization and deterministic reconciliation over pinned source versions."""
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

Domain = Literal["sales", "inventory", "invoices", "payments"]
Kind = Literal["customer", "product", "branch", "invoice", "payment", "transaction"]
REQUIRED = {
    "sales": {"transaction_id", "customer_id", "product_id", "branch_id", "invoice_id", "date", "amount"},
    "inventory": {"transaction_id", "product_id", "branch_id", "date", "quantity", "unit"},
    "invoices": {"invoice_id", "customer_id", "date", "amount"},
    "payments": {"payment_id", "invoice_id", "date", "amount"},
}
UNITS = {
    "ea": ("ea", Decimal(1)), "g": ("kg", Decimal(".001")), "kg": ("kg", Decimal(1)),
    "ml": ("l", Decimal(".001")), "l": ("l", Decimal(1)),
}
FIELDS = {"customer_id": "customer", "product_id": "product", "branch_id": "branch"}
PK = {"sales": "transaction_id", "inventory": "transaction_id", "invoices": "invoice_id", "payments": "payment_id"}
KINDS = {"sales": "transaction", "inventory": "transaction", "invoices": "invoice", "payments": "payment"}
BUSINESS_TZ = ZoneInfo("Asia/Karachi")


class SourceOptions(BaseModel):
    currency: str = Field(default="PKR", pattern=r"^[A-Z]{3}$")
    tax_basis: Literal["gross", "net"] = "gross"
    invoice_source: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")


def normalize_key(value: str) -> str:
    key = unicodedata.normalize("NFKC", value).strip().casefold()
    if not key or len(key) > 120 or any(unicodedata.category(char).startswith("C") for char in key):
        raise ValueError("Invalid identifier")
    return key


def source_date(value: str) -> date:
    # Excel date cells are serialized by the existing parser as midnight.
    value = value.removesuffix(" 00:00:00")
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return date.fromisoformat(value)
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("Timestamp needs a UTC offset")
    return stamp.astimezone(BUSINESS_TZ).date()


def number(value: str) -> Decimal:
    if not re.fullmatch(r"[+-]?[0-9]{1,14}(?:\.[0-9]{1,6})?", value):
        raise ValueError("Invalid fixed-precision amount")
    return Decimal(value)


def money(row: dict[str, str], options: SourceOptions) -> tuple[str, Decimal]:
    currency = row.get("currency") or options.currency
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Invalid currency metadata")
    amount = number(row["amount"])
    tax = number(row.get("tax_amount") or "0")
    if amount < 0 or tax < 0 or (options.tax_basis == "net" and not row.get("tax_amount")):
        raise ValueError("Invalid amount or missing explicit tax")
    return currency, amount + tax if options.tax_basis == "net" else amount


def decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


class Reconciler:
    def __init__(self, aliases: dict[tuple[str, str, str], str], as_of: date) -> None:
        self.aliases = aliases
        self.as_of = as_of
        self.issues: list[dict[str, Any]] = []
        self.unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.invoices: dict[str, dict[str, Any]] = {}
        self.payments: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.sales: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.stock: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        self.stock_units: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.inventory_refs: dict[str, dict[str, Any]] = {}
        self.comparison: list[dict[str, Any]] = []
        self.duplicate_count = 0

    def issue(self, rule: str, *refs: dict[str, Any], severity: str = "material",
              expected: str | None = None, observed: str | None = None) -> None:
        self.issues.append({"rule": rule, "severity": severity, "refs": list(refs),
                            "expected": expected, "observed": observed})

    def mapped(self, source: str, kind: str, value: str, ref: dict[str, Any]) -> str | None:
        try:
            key = normalize_key(value)
        except ValueError:
            key = ""
        mapped = self.aliases.get((source, kind, key))
        if mapped is None:
            self.issue(f"missing_{kind}_mapping", ref, expected=f"Approved {kind} alias", observed=key or None)
        return mapped

    def record(self, source: dict[str, Any], row: dict[str, str], index: int, options: SourceOptions) -> None:
        namespace, domain = source["source"], source["domain"]
        ref = {"version_id": str(source["id"]), "source": namespace, "row": index}
        try:
            key = normalize_key(row.get(PK[domain], ""))
            when = source_date(row.get("date", ""))
        except ValueError:
            self.issue("invalid_identifier_or_date", ref)
            return
        if when > self.as_of:
            return
        canonical = self.aliases.get((namespace, KINDS[domain], key))
        identity = (domain, "", canonical) if canonical else (domain, namespace, key)
        previous = self.unique.get(identity)
        if previous is not None:
            self.duplicate_count += 1
            self.issue("duplicate_invoice" if domain == "invoices" else "duplicate_transaction", previous, ref)
            return
        self.unique[identity] = ref
        entities = {kind: self.mapped(namespace, kind, row.get(field, ""), ref)
                    for field, kind in FIELDS.items() if field in REQUIRED[domain]}
        if domain == "inventory":
            try:
                unit, factor = UNITS[row["unit"].strip().lower()]
                quantity = number(row["quantity"]) * factor
            except (KeyError, ValueError):
                self.issue("invalid_quantity_or_unit", ref)
                return
            product, branch = entities["product"], entities["branch"]
            if product and branch:
                stock_key = (product, branch)
                self.stock_units[stock_key].add(unit)
                self.inventory_refs[product] = ref
                self.stock[(*stock_key, unit)].append({"ref": ref, "quantity": quantity})
            return
        try:
            currency, amount = money(row, options)
            balance = number(row["balance"]) if row.get("balance") else None
            embedded = number(row["invoice_amount"]) if row.get("invoice_amount") else None
            if (balance is not None and balance < 0) or (embedded is not None and embedded < 0):
                raise ValueError
        except ValueError:
            self.issue("invalid_amount_currency_or_tax", ref)
            return
        if domain == "payments":
            self.mapped(namespace, "payment", row["payment_id"], ref)
        invoice = self.mapped(
            namespace if domain == "invoices" else options.invoice_source or namespace,
            "invoice", row.get("invoice_id", ""), ref,
        )
        if invoice is None:
            if domain == "payments":
                self.issue("unmatched_payment", ref)
            return
        entry = {"ref": ref, "currency": currency, "value": amount, "balance": balance,
                 "embedded": embedded, "customer": entities.get("customer"), "product": entities.get("product")}
        if domain == "invoices":
            self.invoices[invoice] = entry
        elif domain == "payments":
            self.payments[invoice].append(entry)
        else:
            self.sales[invoice].append(entry)

    def compare_invoices(self, standalone: bool) -> None:
        for invoice in sorted(set(self.invoices) | set(self.payments) | set(self.sales)):
            invoice_row = self.invoices.get(invoice)
            payments = self.payments[invoice]
            sales = self.sales[invoice]
            if invoice_row is None and not standalone:
                stated = [row for row in payments if row["embedded"] is not None]
                definitions = {(row["currency"], row["embedded"]) for row in stated}
                if len(definitions) == 1:
                    invoice_row = {**stated[0], "value": stated[0]["embedded"], "balance": None}
                elif len(definitions) > 1:
                    self.issue("conflicting_invoice_totals", stated[0]["ref"], stated[-1]["ref"])
            if invoice_row is None:
                for row in payments:
                    self.issue("unmatched_payment", row["ref"])
                if standalone and sales:
                    self.issue("missing_invoice", sales[0]["ref"])
                continue
            currency = invoice_row["currency"]
            compatible_payments = []
            for payment in payments:
                if payment["currency"] != currency:
                    self.issue("currency_mismatch", invoice_row["ref"], payment["ref"],
                               expected=currency, observed=payment["currency"])
                else:
                    compatible_payments.append(payment["value"])
                    if payment["embedded"] is not None and payment["embedded"] != invoice_row["value"]:
                        self.issue("conflicting_invoice_totals", invoice_row["ref"], payment["ref"],
                                   expected=decimal_text(invoice_row["value"]), observed=decimal_text(payment["embedded"]))
            paid = sum(compatible_payments, Decimal(0))
            balance = invoice_row["value"] - paid
            if balance < 0:
                self.issue("payments_exceed_invoice", invoice_row["ref"], *[row["ref"] for row in payments[:10]],
                           expected=decimal_text(invoice_row["value"]), observed=decimal_text(paid))
            if invoice_row["balance"] is not None and invoice_row["balance"] != balance:
                self.issue("invoice_balance_mismatch", invoice_row["ref"],
                           expected=decimal_text(balance), observed=decimal_text(invoice_row["balance"]))
            if sales:
                compatible = all(row["currency"] == currency for row in sales)
                total = sum((row["value"] for row in sales), Decimal(0))
                if not compatible or total != invoice_row["value"]:
                    self.issue("sales_invoice_mismatch", invoice_row["ref"], *[row["ref"] for row in sales[:10]],
                               expected=f"{decimal_text(invoice_row['value'])} {currency}",
                               observed=decimal_text(total) if compatible else "Mixed or different currencies")
                if invoice_row["customer"] and any(row["customer"] != invoice_row["customer"] for row in sales):
                    self.issue("invoice_customer_mismatch", invoice_row["ref"], sales[0]["ref"])
            self.comparison.append({"invoice": invoice, "currency": currency, "amount": decimal_text(invoice_row["value"]),
                                    "paid": decimal_text(paid), "balance": decimal_text(balance),
                                    "partial": Decimal(0) < paid < invoice_row["value"]})

    def result(self, sources: list[dict[str, Any]]) -> dict[str, Any]:
        seen_versions: set[tuple[str, str]] = set()
        for source in sorted(sources, key=lambda row: (row["source"], row["domain"], -row["revision"])):
            ref = {"version_id": str(source["id"]), "source": source["source"], "row": None}
            slot = (source["source"], source["domain"])
            if source["revision"] != source["latest_revision"]:
                self.issue("superseded_source_version", ref)
            if slot in seen_versions:
                self.issue("conflicting_source_versions", ref)
                continue
            seen_versions.add(slot)
            newest = None
            options = SourceOptions.model_validate(source["options"])
            for index, values in enumerate(source["records"], 2):
                row = dict(zip(source["header"], values, strict=True))
                try:
                    when = source_date(row["date"])
                    if when <= self.as_of and (newest is None or when > newest):
                        newest = when
                except (KeyError, ValueError):
                    pass
                self.record(source, row, index, options)
            if newest is None or (self.as_of - newest).days > 45:
                self.issue("stale_source", ref, severity="warning", expected="Activity within 45 days")
        for (product, branch, unit), entries in self.stock.items():
            refs = [row["ref"] for row in entries[:10]]
            if len(self.stock_units[(product, branch)]) > 1:
                self.issue("incompatible_stock_units", *refs)
                continue
            total = sum((row["quantity"] for row in entries), Decimal(0))
            if total < 0:
                self.issue("negative_inventory", *refs, expected=f"At least 0 {unit}", observed=decimal_text(total))
        for entries in self.sales.values():
            for row in entries:
                sales_product = row.get("product")
                if sales_product and sales_product not in self.inventory_refs:
                    self.issue("product_absent_from_inventory", row["ref"], severity="warning")
        standalone = any(source["domain"] == "invoices" for source in sources)
        self.compare_invoices(standalone)
        return {"issues": self.issues, "comparison": self.comparison, "duplicate_count": self.duplicate_count,
                "distinct_transactions": len(self.unique), "invoice_mode": "ledger" if standalone else "payment_rows"}
