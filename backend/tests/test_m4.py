"""Offline model contracts, independent business totals and reporting isolation."""
import os
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.ai_analytics import Explanation, Intent, reporting_engine
from app.ai_provider import Output, ProviderUnavailable
from app.api.v1 import ai
from app.core.tenant import set_org
from app.jobs.imports import import_snapshot
from app.main import app
from app.sql_policy import QueryRejected, validate_sql
from test_m2 import HEADERS, org, register
from test_m3 import INVENTORY, SALES, payments_fixture, publish

ENDPOINT = "/api/v1/ai/queries"


@dataclass(frozen=True)
class Gold:
    question: str
    metric: str
    expected: list[Any]
    options: dict[str, Any] = field(default_factory=dict)


GOLD = (
    Gold("What were gross sales in October?", "gross_sales", ["1800.2345"]),
    Gold("What was October net revenue?", "net_revenue", ["1775.2344"]),
    Gold("What did the goods sold cost in October?", "cogs", ["920.1234"]),
    Gold("How much gross profit did we earn?", "gross_profit", ["855.1110"]),
    Gold("How many completed orders were there?", "order_count", [2]),
    Gold("What was average order value in October?", "average_order_value", ["887.6172"]),
    Gold("How did net revenue grow against the prior comparable period?", "sales_growth", ["77.5234"]),
    Gold("What was revenue for branch B1?", "net_revenue", ["1775"], {"branch": "B1"}),
    Gold("What was revenue for SKU S2?", "net_revenue", ["775"], {"sku": "S2"}),
    Gold("Count orders for customer C2", "order_count", [1], {"customer": "C2"}),
    Gold("Show October revenue by day", "net_revenue", ["1775", "0.2344"], {"dimension": "day"}),
    Gold("Which SKU ranked first by profit?", "gross_profit", ["520.1110"], {"dimension": "sku", "limit": 1}),
    Gold("Count orders on October 31 only", "order_count", [1], {"start": "2026-10-31"}),
    Gold("How many items were on hand on October 31?", "stock_on_hand", ["23"]),
    Gold("How many positions were low on stock?", "low_stock", [1]),
    Gold("How many inventory movements occurred in October?", "inventory_movements", [2]),
    Gold("What was stock on hand in branch B1?", "stock_on_hand", ["20"], {"branch": "B1"}),
    Gold("What did we have in branch B2?", "stock_on_hand", ["3"], {"branch": "B2"}),
    Gold("What was stock on hand on September 30?", "stock_on_hand", ["24"], {"as_of": "2026-09-30"}),
    Gold("Show units by warehouse branch", "stock_on_hand", ["20", "3"], {"dimension": "branch"}),
    Gold("Show stock for SKU S1", "stock_on_hand", ["23"], {"sku": "S1"}),
    Gold("How much was outstanding on October 31?", "outstanding", ["700"]),
    Gold("How much was past due on October 31?", "overdue_amount", ["610"]),
    Gold("How many invoices were overdue?", "overdue_invoices", [7]),
    Gold("How many open invoices were partly paid?", "partial_invoices", [8]),
    Gold("Show outstanding receivables across ageing buckets", "ageing",
         ["90", "160", "180", "180", "90"], {"dimension": "ageing_bucket"}),
    Gold("How much did customer C1 owe on November 1?", "outstanding", ["610"],
         {"as_of": "2026-11-01", "customer": "C1"}),
    Gold("How much did customer C2 owe on October 31?", "outstanding", ["0"], {"customer": "C2"}),
    # I8 contributes 100 until its October 31 payment. I0 and I1 are current on October 30.
    Gold("What was outstanding on October 30?", "outstanding", ["830"], {"as_of": "2026-10-30"}),
    Gold("How much was overdue on October 30?", "overdue_amount", ["640"], {"as_of": "2026-10-30"}),
)


def planned(case: Gold) -> dict[str, Any]:
    sales = case in GOLD[:13] or case.metric == "average_order_value"
    dated_period = sales or case.metric == "inventory_movements"
    return {
        "kind": "group" if "dimension" in case.options else "total", "metric_id": case.metric,
        "dimension": None, "start": "2026-10-01" if dated_period else None,
        "end": "2026-11-01" if dated_period else None, "as_of": None if sales else "2026-10-31",
        "branch": None, "sku": None, "customer": None, "limit": None, **case.options,
    }


class MockProvider:
    def structured(self, schema: type[Output], instructions: str, context: dict[str, object]) -> tuple[Output, int]:
        if schema is Explanation:
            assert set(context) == {"metric", "grouped", "has_value"}
            return schema.model_validate({"emphasis": "scope"}), 8
        assert schema is Intent and "metrics" in context
        case = next((item for item in GOLD if item.question == context["question"]), None)
        if case is None:
            payload = planned(GOLD[0])
            options = {
                "Clarify this ambiguous question": {"kind": "clarify"},
                "Report an unsupported business metric": {"metric_id": "unknown"},
                "Request a grouping beyond the result cap": {"kind": "group", "dimension": "sku", "limit": 101},
                "Request an unsupported dimension": {"kind": "group", "dimension": "ageing_bucket"},
                "Request an invalid reporting period": {"end": "2026-10-01"},
                "Request a forbidden domain filter": {"as_of": "2026-10-31"},
                "What stock is available after reservations?": {
                    "metric_id": "available_stock", "start": None, "end": None, "as_of": "2026-10-31",
                },
                "Average order value for a missing customer": {"metric_id": "average_order_value", "customer": "none"},
            }
            payload.update(options[str(context["question"])])
        else:
            payload = planned(case)
        return schema.model_validate(payload), 21


@pytest.fixture(scope="module")
def workspace():
    if os.getenv("M4_INTEGRATION") != "1":
        pytest.skip("M4_INTEGRATION=1 requires the isolated M4 database")
    with (
        TestClient(app, base_url="http://localhost", headers=HEADERS) as first,
        TestClient(app, base_url="http://localhost", headers=HEADERS) as second,
    ):
        register(first)
        credentials = register(second)
        first_org, second_org = org(first), org(second)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(import_snapshot, "apply_async", lambda args, **kwargs: import_snapshot.run(*args))
            publish(first, first_org, "sales", SALES)
            publish(first, first_org, "inventory", INVENTORY)
            publish(first, first_org, "payments", payments_fixture())
        yield first, second, first_org.rsplit("/", 1)[1], second_org.rsplit("/", 1)[1], credentials


@pytest.fixture
def evaluated(workspace, monkeypatch):
    monkeypatch.setattr(ai, "get_provider", MockProvider)
    monkeypatch.setattr(ai, "limit_key", lambda *_: None)
    return workspace


@pytest.mark.parametrize("case", GOLD, ids=lambda case: case.question)
def test_gold_business_questions(evaluated, case: Gold) -> None:
    first, _, organization, _, _ = evaluated
    response = first.post(ENDPOINT, json={"organization_id": organization, "question": case.question})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "completed", value
    assert value["evidence"]["metric_id"] == case.metric and value["evidence"]["metric_version"] == 1
    assert value["evidence"]["dataset_versions"] and value["evidence"]["query_hash"].startswith("sha256:")
    column = value["result"]["columns"][-1]
    actual = [row[column] for row in value["result"]["rows"]]
    assert actual == case.expected
    if len(value["result"]["columns"]) == 1:
        assert f" is {actual[0]}." in value["answer"]
    else:
        assert f"across {len(actual)} groups" in value["answer"]
    assert value["usage"]["llm_tokens"] == 29 and value["usage"]["latency_ms"] >= 0


@pytest.mark.parametrize("query", [
    "SELECT 1", "SELECT name FROM synthetic_items", "SELECT org_id FROM unrelated.rows",
    "SELECT owner FROM reporting.rows", "SELECT payload->>'unapproved' FROM reporting.rows",
    "SELECT org_id FROM reporting.rows; SELECT 1", "SELECT lower(domain) FROM reporting.sources",
    "SELECT org_id FROM reporting.rows WHERE org_id=:unapproved",
    "SELECT org_id FROM reporting.rows UNION SELECT org_id FROM reporting.sources",
    "SELECT org_id FROM reporting.rows FOR UPDATE", "SELECT org_id INTO example FROM reporting.rows",
    "SELECT org_id FROM reporting.rows /* test comment */",
    "SELECT CAST(domain AS UUID) FROM reporting.sources",
    "INSERT INTO example VALUES (1)", "CREATE TABLE example (id integer)",
])
def test_sql_policy_rejections(query: str) -> None:
    with pytest.raises(QueryRejected):
        validate_sql(query)


@pytest.mark.parametrize(("question", "status"), [
    ("Clarify this ambiguous question", "clarification_required"),
    ("Report an unsupported business metric", "unsupported_metric"),
    ("Request a grouping beyond the result cap", "query_rejected"),
    ("Request an unsupported dimension", "query_rejected"),
    ("Request an invalid reporting period", "query_rejected"),
    ("Request a forbidden domain filter", "query_rejected"),
    ("What stock is available after reservations?", "insufficient_data"),
    ("Average order value for a missing customer", "insufficient_data"),
])
def test_non_success_outcomes(evaluated, question: str, status: str) -> None:
    first, _, organization, _, _ = evaluated
    result = first.post(ENDPOINT, json={"organization_id": organization, "question": question})
    assert result.status_code == 200 and result.json()["status"] == status


def test_history_reporting_role_and_tenant_boundaries(evaluated) -> None:
    first, second, organization, other_org, credentials = evaluated
    body = {"organization_id": organization, "question": GOLD[0].question}
    assert first.post(ENDPOINT, json=body, headers={"X-CSRF": ""}).status_code == 403
    assert second.post(ENDPOINT, json=body).status_code == 404
    assert first.post(ENDPOINT, json={**body, "sql": "SELECT 1"}).status_code == 422
    assert first.post(ENDPOINT, json={**body, "dataset_ids": [str(uuid4())]}).json()["status"] == "insufficient_data"
    result = first.post(ENDPOINT, json=body).json()
    target = ENDPOINT + "/" + result["query_id"]
    scope = {"organization_id": organization}
    assert second.get(target, params=scope).status_code == 404
    assert second.get(target, params={"organization_id": other_org}).status_code == 404
    assert first.get(target, params=scope).headers["cache-control"] == "private, no-store"
    invitation = first.post(f"/api/v1/organizations/{organization}/invitations", json={
        "email": credentials["email"], "role": "viewer",
    })
    assert second.post(f"/api/v1/organizations/{organization}/invitations/accept", json=invitation.json()).status_code == 204
    assert second.get(target, params=scope).status_code == 404
    assert second.get(ENDPOINT, params=scope).json() == []
    assert first.get(target, params=scope).json()["result"] == result["result"]
    with reporting_engine().begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM reporting.rows")).scalar_one() == 0
        set_org(connection, other_org)
        assert connection.execute(text("SELECT count(*) FROM reporting.rows")).scalar_one() == 0
        set_org(connection, organization)
        assert connection.execute(text("SELECT count(*) FROM reporting.rows")).scalar_one() > 0
        assert not connection.execute(text("SELECT has_schema_privilege(current_user,'app','USAGE')")).scalar_one()
        assert not connection.execute(text("SELECT has_schema_privilege(current_user,'biz','USAGE')")).scalar_one()
        assert not connection.execute(text(
            "SELECT has_table_privilege(current_user,'reporting.rows','INSERT')"
        )).scalar_one()
    version = result["evidence"]["dataset_versions"][0]
    lifecycle = f"/api/v1/organizations/{organization}/datasets/versions/{version}"
    assert first.post(lifecycle + "/archive").status_code == 204
    try:
        assert first.get(target, params=scope).json()["evidence"] == result["evidence"]
        assert first.post(ENDPOINT, json={**body, "dataset_ids": [version]}).json()["status"] == "insufficient_data"
    finally:
        assert first.post(lifecycle + "/restore").status_code == 204


def test_provider_outage_keeps_dashboard_operational(evaluated, monkeypatch) -> None:
    first, _, organization, _, _ = evaluated

    def unavailable():
        raise ProviderUnavailable("provider unavailable")

    monkeypatch.setattr(ai, "get_provider", unavailable)
    result = first.post(ENDPOINT, json={"organization_id": organization, "question": GOLD[0].question})
    assert result.json()["status"] == "execution_error"
    assert first.get(f"/api/v1/organizations/{organization}/analytics/overview").status_code == 200
