"""Independent arithmetic, calendar boundaries and tenant authorization for BI."""
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from test_m2 import integration as m2_integration
from test_m2 import org, register

from app.api.v1.analytics import Filters, resolve_period
from app.ingestion import FIELDS, Domain
from app.jobs.imports import import_snapshot
from app.semantic import catalog

integration = m2_integration
PERIOD = "start=2026-10-01&end=2026-11-01"
SALES = """line_id,order_id,customer_id,sku,branch_id,ordered_on,quantity,unit_price,discount,unit_cost,status,currency
L1,O1,C1,S1,B1,2026-10-01,4,250,0,120,completed,PKR
L2,O1,C1,S2,B1,2026-10-01,2,400,25,220,completed,PKR
L3,O2,C2,S1,B2,2026-10-31,1,0.2345,0.0001,0.1234,completed,PKR
L4,O3,C1,S1,B1,2026-08-31,1,1000,0,500,completed,PKR
L5,O4,C1,S1,B1,2026-08-30,1,999,0,500,completed,PKR
L6,O5,C1,S1,B1,2026-11-01,1,999,0,500,completed,PKR
L7,O6,C1,S1,B1,2026-10-01,1,999,0,500,cancelled,PKR
L8,O7,C1,S1,B1,2026-10-01,1,999,0,500,pending,PKR
"""
INVENTORY = """movement_id,sku,branch_id,movement_on,quantity,movement_type
M1,S1,B1,2026-09-30,24,receipt
M2,S1,B1,2026-10-01,-4,shipment
M3,S1,B2,2026-10-31,3,receipt
M4,S1,B2,2026-11-01,80,receipt
"""


def payments_fixture() -> str:
    result = ["payment_id,invoice_id,customer_id,invoice_on,due_on,paid_on,invoice_amount,amount,currency"]
    as_of = date(2026, 10, 31)
    for index, overdue in enumerate((0, 1, 30, 31, 60, 61, 90, 91)):
        due = as_of - timedelta(days=overdue)
        result.append(f"P{index},I{index},C1,2026-01-01,{due},{due},100,10,PKR")
    result.extend([
        "P8,I0,C1,2026-01-01,2026-10-31,2026-11-01,100,90,PKR",
        "P9,I1,C1,2026-01-01,2026-10-30,2026-10-31,100,20,PKR",
        "P10,I8,C2,2026-01-01,2026-04-01,2026-10-31,100,100,PKR",
        "P11,I9,C2,2026-11-01,2026-11-01,2026-11-01,999,0,PKR",
    ])
    return "\n".join(result) + "\n"


def publish(client, path: str, domain: Domain, content: str) -> str:
    upload = client.post(path + "/datasets/uploads", files={"file": (f"{domain}.csv", content.encode())})
    assert upload.status_code == 201, upload.text
    response = client.post(path + "/datasets/imports", json={
        "upload_id": upload.json()["id"], "domain": domain, "mapping": {field: field for field in FIELDS[domain]},
    })
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert client.get(path + f"/jobs/{job_id}").json()["status"] == "published"
    return job_id


def test_business_period_and_versioned_catalog() -> None:
    period = resolve_period(Filters(), date(2026, 12, 15))
    assert (period["start"], period["end"], period["as_of"], period["prior_start"]) == (
        date(2026, 12, 1), date(2027, 1, 1), date(2026, 12, 15), date(2026, 10, 31),
    )
    metrics = catalog()
    assert len(metrics) == len({row["metric_id"] for row in metrics}) == 16
    assert all(row["version"] == 1 and row["owner"] and row["required_tables"] for row in metrics)


def test_kpis_filters_snapshots_and_roles(integration, monkeypatch) -> None:
    admin, other = integration
    credentials = register(other)
    register(admin)
    first, second = org(admin), org(other)
    monkeypatch.setattr(import_snapshot, "apply_async", lambda args, **kwargs: import_snapshot.run(*args))
    sales_id = publish(admin, first, "sales", SALES)
    publish(admin, first, "inventory", INVENTORY)
    publish(admin, first, "payments", payments_fixture())
    for endpoint in ("metrics", "analytics/overview", "analytics/sales", "analytics/inventory", "analytics/receivables"):
        assert other.get(first + "/" + endpoint).status_code == 404
    assert other.get(second + "/analytics/sales").json()["reports"]["sales"] == {"source": None, "metrics": None}
    invitation = admin.post(first + "/invitations", json={"email": credentials["email"], "role": "viewer"})
    assert other.post(first + "/invitations/accept", json=invitation.json()).status_code == 204
    assert other.get(first + "/metrics").status_code == 200

    def report(domain: str, extra: str = "", client=other):
        response = client.get(first + f"/analytics/{domain}?{PERIOD}{extra}")
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        return response.json()["reports"]

    sales = report("sales")["sales"]
    assert sales["source"]["version"] == sales_id
    assert sales["source"]["published_at"]
    assert {key: Decimal(value) for key, value in sales["metrics"].items()} == {
        "gross_sales": Decimal("1800.2345"), "net_revenue": Decimal("1775.2344"),
        "cogs": Decimal("920.1234"), "gross_profit": Decimal("855.1110"),
        "order_count": Decimal(2), "average_order_value": Decimal("887.6172"),
        "prior_net_revenue": Decimal(1000), "sales_growth": Decimal("77.5234"),
    }
    assert [row["day"] for row in sales["trend"]] == ["2026-10-01", "2026-10-31"]
    filtered = report("sales", "&" + urlencode({"branch": "B1", "sku": "S2", "customer": "C1"}))["sales"]["metrics"]
    assert filtered["order_count"] == 1
    assert Decimal(filtered["net_revenue"]) == Decimal(775)
    assert filtered["sales_growth"] is None
    assert report("sales", "&customer=missing")["sales"]["metrics"]["average_order_value"] is None

    inventory = report("inventory")["inventory"]
    assert inventory["metrics"] == {
        "stock_on_hand": "23", "available_stock": None, "low_stock": 1, "inventory_movements": 2,
    }
    assert [row["stock_on_hand"] for row in inventory["positions"]] == ["3", "20"]
    early = report("inventory", "&as_of=2026-10-01&branch=B1&sku=S1&threshold=20")["inventory"]
    assert early["metrics"]["stock_on_hand"] == "20" and early["metrics"]["low_stock"] == 1
    assert early["movements"] == [{"movement_type": "shipment", "movement_count": 1, "net_quantity": "-4"}]

    receivables = report("receivables")["receivables"]
    assert receivables["metrics"] == {
        "outstanding": "700", "overdue_amount": "610", "overdue_invoices": 7, "partial_invoices": 8,
    }
    assert [(row["bucket"], row["balance"]) for row in receivables["ageing"]] == [
        ("Current", "90"), ("1–30", "160"), ("31–60", "180"), ("61–90", "180"), ("91+", "90"),
    ]
    assert report("receivables", "&as_of=2026-11-01&customer=C1")["receivables"]["metrics"]["outstanding"] == "610"
    assert other.get(first + "/analytics/sales?start=2026-10-01&end=2026-10-01").status_code == 422
    assert other.get(first + "/analytics/inventory?threshold=-1").status_code == 422
    assert set(report("overview")) == {"sales", "inventory", "receivables"}

    replacement = SALES.splitlines()
    new_id = publish(admin, first, "sales", "\n".join(replacement[:2]) + "\n")
    latest = report("sales")["sales"]
    assert latest["source"]["version"] == new_id != sales_id
    assert Decimal(latest["metrics"]["net_revenue"]) == Decimal(1000)
    assert other.get(second + "/analytics/sales").json()["reports"]["sales"]["metrics"] is None
