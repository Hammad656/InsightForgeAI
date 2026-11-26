"""Bounded tabular parsing and snapshot validation."""
import csv
import io
import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import PurePath
from typing import Literal
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException

from openpyxl import load_workbook

Domain = Literal["sales", "inventory", "payments"]
FIELDS: dict[Domain, tuple[str, ...]] = {
    "sales": ("line_id", "order_id", "customer_id", "sku", "branch_id", "ordered_on",
              "quantity", "unit_price", "discount", "unit_cost", "status", "currency"),
    "inventory": ("movement_id", "sku", "branch_id", "movement_on", "quantity", "movement_type"),
    "payments": ("payment_id", "invoice_id", "customer_id", "invoice_on", "due_on", "paid_on",
                 "invoice_amount", "amount", "currency"),
}
MAX_BYTES = 5 * 1024**2
MAX_ROWS = 10_000
MAX_COLUMNS = 64
MAX_CELL = 1024
SIGNED_NUMBER = re.compile(r"[+-][0-9]+(?:\.[0-9]+)?")


class InvalidFile(ValueError):
    pass


HEADER_ALIASES = {
    "order_id": ("ordernumber",),
    "customer_id": ("customercode",),
    "sku": ("productcode",),
    "ordered_on": ("transactiondate",),
    "unit_price": ("sellingprice",),
    "branch_id": ("branchcode",),
}


def suggest_mapping(headers: list[str], domain: Domain) -> tuple[dict[str, str], list[str], list[str]]:
    normalized_headers: dict[str, list[str]] = {}
    for header in headers:
        key = re.sub(r"[^a-z0-9]", "", header.casefold())
        normalized_headers.setdefault(key, []).append(header)
    mapping: dict[str, str] = {}
    ambiguous: list[str] = []
    for field in FIELDS[domain]:
        keys = (field.replace("_", ""), *HEADER_ALIASES.get(field, ()))
        candidates = [header for key in keys for header in normalized_headers.get(key, ())]
        if len(candidates) == 1:
            mapping[field] = candidates[0]
        elif candidates:
            ambiguous.append(field)
    duplicates = {header for header in mapping.values() if list(mapping.values()).count(header) > 1}
    for field in list(mapping):
        if mapping[field] in duplicates:
            ambiguous.append(field)
            del mapping[field]
    missing = [field for field in FIELDS[domain] if field not in mapping and field not in ambiguous]
    return mapping, missing, ambiguous


def xlsx_rows(content: bytes) -> list[tuple[str, ...]]:
    with ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if (len(members) > 100 or sum(member.file_size for member in members) > 24 * 1024**2
                or any("vbaproject" in member.filename.lower() for member in members)):
            raise InvalidFile("Workbook exceeds supported limits or contains macros")
    workbook = load_workbook(io.BytesIO(content), read_only=True, keep_links=False)
    try:
        if len(workbook.worksheets) != 1:
            raise InvalidFile("Upload a workbook with exactly one sheet")
        sheet = workbook.worksheets[0]
        sheet.reset_dimensions()
        rows: list[tuple[str, ...]] = []
        for row in sheet.iter_rows():
            if len(rows) > MAX_ROWS or len(row) > MAX_COLUMNS:
                raise InvalidFile("Workbook exceeds the row or column limit")
            if any(cell.data_type in {"f", "e"} for cell in row):
                raise InvalidFile("Workbook cells must contain values, without formulas or spreadsheet errors")
            rows.append(tuple(str(cell.value) if cell.value is not None else "" for cell in row))
        return rows
    finally:
        workbook.close()


def parse(filename: str, content: bytes) -> tuple[list[str], list[list[str]]]:
    if not content or len(content) > MAX_BYTES:
        raise InvalidFile("File must contain data and be at most 5 MiB")
    suffix = PurePath(filename).suffix.lower()
    raw: Iterator[tuple[str, ...] | list[str]]
    try:
        if suffix == ".xlsx":
            raw = iter(xlsx_rows(content))
        elif suffix == ".csv" and not content.startswith(b"PK\x03\x04"):
            raw = iter(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True))
        else:
            raise InvalidFile("Use a UTF-8 CSV or XLSX file")
        headers = [cell.strip() for cell in next(raw, ())]
        if not 1 <= len(headers) <= MAX_COLUMNS or any(not cell for cell in headers):
            raise InvalidFile("Provide a nonempty header for each column")
        if len(headers) != len(set(headers)):
            raise InvalidFile("Column names must be unique")
        rows: list[list[str]] = []
        for row in raw:
            if not row or not any(row):
                continue
            if len(rows) >= MAX_ROWS or len(row) > len(headers) or (suffix == ".csv" and len(row) != len(headers)):
                raise InvalidFile("Row count or column count exceeds the supported layout")
            rows.append([str(cell).strip() for cell in row] + [""] * (len(headers) - len(row)))
        if not rows or any(len(cell) > MAX_CELL or "\x00" in cell for row in [headers, *rows] for cell in row):
            raise InvalidFile("File must have data rows and cells of at most 1,024 characters")
        if any(
            cell.startswith(("=", "+", "-", "@")) and not SIGNED_NUMBER.fullmatch(cell)
            for row in [headers, *rows]
            for cell in row
        ):
            raise InvalidFile("Spreadsheet formulas are not supported")
        return headers, rows
    except InvalidFile:
        raise
    except (BadZipFile, UnicodeError, csv.Error, ValueError, KeyError, OSError, RuntimeError, ParseError, DefusedXmlException, TypeError, IndexError) as exc:
        raise InvalidFile("File content is invalid") from exc


def normalized(field: str, value: str, domain: Domain) -> str:
    if field.endswith("_on"):
        return date.fromisoformat(value.removesuffix(" 00:00:00")).isoformat()
    if field in {"quantity", "unit_price", "discount", "unit_cost", "invoice_amount", "amount"}:
        if not re.fullmatch(r"-?\d{1,12}(?:\.\d{1,4})?", value):
            raise ValueError
        number = Decimal(value)
        if domain == "inventory" and field == "quantity":
            if not number:
                raise ValueError
        elif number < 0 or (field == "quantity" and not number):
            raise ValueError
        return format(number.normalize(), "f")
    if field == "currency" and value != "PKR":
        raise ValueError
    if field == "status" and value not in {"completed", "cancelled", "pending"}:
        raise ValueError
    if field == "movement_type" and value not in {"receipt", "shipment", "adjustment", "return"}:
        raise ValueError
    if not value or (field.endswith("_id") or field == "sku") and len(value) > 120:
        raise ValueError
    return value


def validate(domain: Domain, mapping: dict[str, str], headers: list[str], rows: list[list[str]]) -> tuple[
    list[dict[str, str]], list[dict[str, int | str]]
]:
    fields = FIELDS[domain]
    if (set(mapping) != set(fields) or len(set(mapping.values())) != len(fields)
            or not set(mapping.values()).issubset(headers)):
        return [], [{"row": 1, "message": "Map each required field to a different source column"}]
    indexes = {field: headers.index(mapping[field]) for field in fields}
    seen: set[str] = set()
    references: dict[str, tuple[str, ...]] = {}
    totals: dict[str, Decimal] = {}
    valid = []
    errors: list[dict[str, int | str]] = []
    for number, row in enumerate(rows, start=2):
        record = {}
        for field, index in indexes.items():
            try:
                record[field] = normalized(field, row[index], domain)
            except ValueError:
                errors.append({"row": number, "message": f"Invalid {field}"})
                break
        else:
            key = record[fields[0]]
            if key in seen:
                errors.append({"row": number, "message": "Duplicate source identifier"})
                continue
            seen.add(key)
            error = validate_relationships(domain, record, references, totals)
            if error:
                errors.append({"row": number, "message": error})
                continue
            valid.append(record)
    return valid, errors


def validate_relationships(
    domain: Domain, record: dict[str, str], references: dict[str, tuple[str, ...]], totals: dict[str, Decimal],
) -> str | None:
    if domain == "inventory":
        movement = record["movement_type"]
        quantity = Decimal(record["quantity"])
        if (movement == "shipment" and quantity > 0) or (movement in {"receipt", "return"} and quantity < 0):
            return "Movement quantity sign conflicts with its movement type"
        return None

    if domain == "sales":
        if Decimal(record["discount"]) > Decimal(record["quantity"]) * Decimal(record["unit_price"]):
            return "Line discount exceeds gross line amount"
        reference = record["order_id"]
        fields = ("customer_id", "branch_id", "ordered_on", "status", "currency")
    else:
        if record["due_on"] < record["invoice_on"] or record["paid_on"] < record["invoice_on"]:
            return "Due and payment dates must be on or after the invoice date"
        reference = record["invoice_id"]
        fields = ("customer_id", "invoice_on", "due_on", "invoice_amount", "currency")

    attributes = tuple(record[field] for field in fields)
    if reference in references and references[reference] != attributes:
        return "Related rows disagree on the order or invoice details"
    if domain == "payments":
        total = totals.get(reference, Decimal(0)) + Decimal(record["amount"])
        if total > Decimal(record["invoice_amount"]):
            return "Payments exceed the invoice amount"
        totals[reference] = total
    references[reference] = attributes
    return None
