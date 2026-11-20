"""Deterministic reconciliation fixtures and publication safety checks."""
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.api.v1 import ai, reconciliation, reports
from app.core.config import get_settings
from app.core.database import get_engine
from app.core.tenant import set_org
from app.executive_report import render
from app.jobs.imports import import_snapshot
from app.jobs.reconciliation import reconcile_sources
from app.jobs.reports import generate_report
from app.main import app
from app.reconciliation import Reconciler, SourceOptions, money, normalize_key, number, source_date
from test_m2 import HEADERS, org, register
from test_m3 import SALES, publish
from test_m4 import MockProvider
from test_m5 import extracted

ROOT = Path(__file__).parent / "fixtures" / "m6a"
RUNS = "/api/v1/reconciliation-runs"
SOURCES = "/api/v1/reconciliation-sources"
MAPPINGS = "/api/v1/reconciliation-mappings"
ISSUES = "/api/v1/reconciliation-issues"
AS_OF = "2026-10-31"
ALIASES = [
    ("erp", "customer", "C-ERP", "customer-1"), ("ledger", "customer", "C-LEDGER", "customer-1"),
    ("erp", "product", "P-ERP", "product-1"), ("warehouse", "product", "P-WH", "product-1"),
    ("erp", "branch", "B-ERP", "branch-1"), ("warehouse", "branch", "B-WH", "branch-1"),
    ("ledger", "invoice", "I-1", "invoice-1"), ("ledger", "invoice", "I-2", "invoice-2"),
    ("bank", "payment", "P-1", "payment-1"), ("bank", "payment", "P-2", "payment-2"),
    ("bank", "payment", "P-3", "payment-3"), ("bank", "payment", "P-4", "payment-4"),
]


def test_exact_normalization_and_no_implicit_fx() -> None:
    assert normalize_key(" Ｉ-1 ") == "i-1"
    assert source_date("2026-10-01T20:00:00+00:00") == date(2026, 10, 2)
    assert source_date("2026-10-01") == date(2026, 10, 1)
    with pytest.raises(ValueError):
        source_date("2026-10-01T20:00:00")
    with pytest.raises(ValueError):
        number("1.0000001")
    assert source_date("2026-10-01 00:00:00") == date(2026, 10, 1)
    assert money({"amount": "1.000001", "tax_amount": "0.25", "currency": "USD"},
                 SourceOptions(tax_basis="net")) == ("USD", Decimal("1.250001"))
    with pytest.raises(ValueError):
        money({"amount": "1"}, SourceOptions(tax_basis="net"))


def test_cross_source_duplicate_and_stale_revision() -> None:
    def payment(source_name, key, revision, latest):
        return {
            "source": source_name, "domain": "payments", "id": uuid4(), "revision": revision,
            "latest_revision": latest, "options": {"invoice_source": "ledger"},
            "header": ["payment_id", "invoice_id", "date", "amount", "invoice_amount"],
            "records": [[key, "inv", "2026-08-01", "20.000001", "80"]],
        }

    aliases = {("ledger", "invoice", "inv"): "invoice",
               ("bank-a", "payment", "one"): "same-payment",
               ("bank-b", "payment", "two"): "same-payment"}
    result = Reconciler(aliases, date(2026, 10, 31)).result([
        payment("bank-a", "one", 1, 2),
        payment("bank-a", "one", 2, 2),
        payment("bank-b", "two", 1, 1),
    ])
    assert result["invoice_mode"] == "payment_rows"
    assert result["duplicate_count"] == 1
    assert result["comparison"] == [{
        "invoice": "invoice", "currency": "PKR", "amount": "80", "paid": "20.000001",
        "balance": "59.999999", "partial": True,
    }]
    assert {finding["rule"] for finding in result["issues"]} == {
        "superseded_source_version", "conflicting_source_versions", "duplicate_transaction", "stale_source",
    }


def alias(client, scope, namespace, kind, key, canonical_key, revision=0):
    return client.post(MAPPINGS, params=scope, json={
        "source": namespace, "kind": kind, "source_key": key, "canonical_key": canonical_key,
        "expected_revision": revision,
    })


def source(client, organization, domain, namespace, data=None):
    return client.post(SOURCES, data={
        "organization_id": organization, "domain": domain, "source": namespace, "invoice_source": "ledger",
    }, files={"file": (f"{domain}.csv", data if data is not None else (ROOT / f"{domain}.csv").read_bytes())})


@pytest.mark.skipif(os.getenv("M6A_INTEGRATION") != "1", reason="requires isolated PostgreSQL and Redis")
def test_full_reconciliation_quality_and_roles(monkeypatch) -> None:
    with (
        TestClient(app, base_url="http://localhost", headers=HEADERS) as owner,
        TestClient(app, base_url="http://localhost", headers=HEADERS) as outsider,
    ):
        register(owner)
        other = register(outsider)
        path = org(owner)
        org(outsider)
        organization = path.rsplit("/", 1)[1]
        scope = {"organization_id": organization}
        monkeypatch.setattr(import_snapshot, "apply_async", lambda args, **kwargs: import_snapshot.run(*args))
        publish(owner, path, "sales", SALES)
        monkeypatch.setattr(ai, "get_provider", MockProvider)
        monkeypatch.setattr(reports, "enqueue", lambda *_: None)
        monkeypatch.setattr(reconciliation, "enqueue", lambda *_: None)
        queued_report = owner.post("/api/v1/reports", json={
            **scope, "request_key": str(uuid4()), "start": "2026-10-01", "end": "2026-11-01",
        })
        assert queued_report.status_code == 202
        inputs = []
        for domain, namespace in [("sales", "erp"), ("inventory", "warehouse"), ("invoices", "ledger"), ("payments", "bank")]:
            upload = source(owner, organization, domain, namespace)
            assert upload.status_code == 201, upload.text
            assert upload.json()["row_count"] > 0
            inputs.append(upload.json()["id"])
        assert source(owner, organization, "sales", "erp").json()["id"] == inputs[0]
        for mapping in ALIASES:
            result = alias(owner, scope, *mapping)
            assert result.status_code == 201, result.text
        assert alias(owner, scope, "erp", "product", "p-erp", "wrong-target").status_code == 409
        assert outsider.get(SOURCES, params=scope).status_code == 404
        assert outsider.post(RUNS, json={**scope, "input_ids": inputs, "request_key": str(uuid4()), "as_of": AS_OF}).status_code == 404

        def start(selected):
            body = {**scope, "input_ids": selected, "request_key": str(uuid4()), "as_of": AS_OF}
            response = owner.post(RUNS, json=body)
            assert response.status_code == 202, response.text
            job = response.json()
            assert owner.post(RUNS, json=body).json()["id"] == job["id"]
            return job["id"]

        def execute(run_id):
            if os.getenv("M6A_LIVE_WORKER") == "1":
                reconcile_sources.apply_async(args=(organization, run_id))
                limit = monotonic() + 20
                while monotonic() < limit:
                    response = owner.get(f"{RUNS}/{run_id}", params=scope).json()
                    if response["status"] != "queued":
                        break
                    sleep(.1)
            reconcile_sources.run(organization, run_id)
            reconcile_sources.run(organization, run_id)
            return owner.get(f"{RUNS}/{run_id}", params=scope).json()

        run_id = start(inputs)
        if os.getenv("M6A_LIVE_WORKER") == "1":
            # Celery must transition the first job without a synchronous substitute.
            reconcile_sources.apply_async(args=(organization, run_id))
            deadline = monotonic() + 20
            while monotonic() < deadline:
                if owner.get(f"{RUNS}/{run_id}", params=scope).json()["status"] == "completed":
                    break
                sleep(.1)
            else:
                pytest.fail("Live reconciliation worker did not complete the run")
        run = execute(run_id)
        assert run["status"] == "completed"
        assert run["summary"]["duplicate_count"] == 3
        assert run["summary"]["comparison"] == [
            {"invoice": "invoice-1", "amount": "100", "paid": "60", "balance": "40", "partial": True, "currency": "PKR"},
            {"invoice": "invoice-2", "amount": "25", "paid": "0", "balance": "25", "partial": False, "currency": "PKR"},
        ]
        assert owner.get(f"{RUNS}/{run_id}/comparison", params=scope).json() == run["summary"]["comparison"]
        findings = owner.get(f"{RUNS}/{run_id}/issues", params=scope).json()
        rules = [finding["rule"] for finding in findings]
        assert len(findings) == run["summary"]["issue_count"]
        assert rules.count("duplicate_transaction") == 2 and rules.count("duplicate_invoice") == 1
        assert set(rules) == {
            "duplicate_invoice", "duplicate_transaction", "missing_customer_mapping", "missing_product_mapping",
            "missing_branch_mapping", "missing_invoice_mapping", "unmatched_payment", "currency_mismatch",
            "negative_inventory", "invoice_balance_mismatch", "sales_invoice_mismatch", "invoice_customer_mismatch",
        }
        balance = next(finding for finding in findings if finding["rule"] == "invoice_balance_mismatch")
        assert (balance["expected"], balance["observed"]) == ("40", "30")
        assert next(finding for finding in findings if finding["rule"] == "negative_inventory")["observed"] == "-1.5"
        target = ISSUES + "/" + balance["id"]
        assert outsider.get(f"{RUNS}/{run_id}/issues", params=scope).status_code == 404
        assert outsider.patch(target, params=scope, json={"status": "reviewed", "note": "Unauthorized review"}).status_code == 404
        invitation = owner.post(path + "/invitations", json={"email": other["email"], "role": "analyst"})
        assert outsider.post(path + "/invitations/accept", json=invitation.json()).status_code == 204
        assert outsider.patch(target, params=scope, json={"status": "acknowledged", "note": "Unauthorized override"}).status_code == 403
        assert outsider.post(MAPPINGS, params=scope, json={
            "source": "erp", "kind": "product", "source_key": "F", "canonical_key": "F",
        }).status_code == 403

        analytics = owner.get(path + "/analytics/sales?start=2026-10-01&end=2026-11-01").json()
        assert analytics["data_quality"]["state"] == "blocked"
        assert analytics["reports"]["sales"]["metrics"]["net_revenue"] == "1775.2344"
        ai_result = owner.post("/api/v1/ai/queries", json={
            **scope, "question": "What was October net revenue?",
        }).json()
        assert ai_result["evidence"]["data_quality"]["state"] == "blocked"
        report_body = {**scope, "request_key": str(uuid4()), "start": "2026-10-01", "end": "2026-11-01"}
        assert owner.post("/api/v1/reports", json=report_body).status_code == 409
        report_id = queued_report.json()["id"]
        generate_report.run(organization, report_id)
        assert owner.get(f"/api/v1/reports/{report_id}", params=scope).json()["error_code"] == "data_quality_blocked"

        branch = next(finding for finding in findings if finding["rule"] == "missing_branch_mapping")
        branch_url = ISSUES + "/" + branch["id"]
        assert owner.patch(branch_url, params=scope, json={"status": "reviewed", "note": "Reviewed missing branch"}).status_code == 200
        assert owner.patch(branch_url, params=scope, json={"status": "resolved", "note": "Unverified correction"}).status_code == 409
        created = alias(owner, scope, "erp", "branch", "B-MISSING", "branch-1")
        corrected = alias(owner, scope, "erp", "branch", "B-MISSING", "branch-2", revision=1)
        assert created.json()["revision"] == 1 and corrected.json()["revision"] == 2
        replacement = source(owner, organization, "sales", "erp", (ROOT / "sales.csv").read_bytes() + b"\n")
        assert replacement.json()["revision"] == 2 and replacement.json()["id"] != inputs[0]
        next_run = start([replacement.json()["id"], *inputs[1:]])
        with monkeypatch.context() as patch:
            def transient(*_):
                raise OperationalError(None, None, OSError("synthetic"))

            patch.setattr(Reconciler, "result", transient)
            with pytest.raises(OperationalError):
                reconcile_sources.run(organization, next_run)
        assert owner.get(f"{RUNS}/{next_run}", params=scope).json()["status"] == "queued"
        assert owner.get(f"{RUNS}/{next_run}/issues", params=scope).json() == []
        execute(next_run)
        assert owner.patch(branch_url, params=scope, json={
            "status": "resolved", "note": "Corrected mapping verified by rerun", "correction_alias_id": corrected.json()["id"],
        }).status_code == 200
        assert owner.get(branch_url + "/reviews", params=scope).json()[-1]["correction_alias_id"] == corrected.json()["id"]
        pinned = owner.get(f"{RUNS}/{next_run}/mappings", params=scope).json()
        assert corrected.json()["id"] in {mapping["id"] for mapping in pinned}
        for current_run in (run_id, next_run):
            for issue in owner.get(f"{RUNS}/{current_run}/issues", params=scope).json():
                if issue["severity"] != "material" or issue["status"] in {"resolved", "acknowledged"}:
                    continue
                url = ISSUES + "/" + issue["id"]
                assert owner.patch(url, params=scope, json={"status": "reviewed", "note": "Reviewed the exact source rows"}).status_code == 200
                assert owner.patch(url, params=scope, json={"status": "acknowledged", "note": "Known synthetic limitation"}).status_code == 200
        assert owner.get(target + "/reviews", params=scope).json()[-1]["status"] == "acknowledged"
        accepted = owner.post("/api/v1/reports", json=report_body)
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["filters"]["data_quality"]["state"] == "acknowledged"
        pdf = extracted(render(uuid4(), "Synthetic", accepted.json()["filters"], {}, {}))
        assert "acknowledged material exceptions" in pdf and next_run in pdf
        with get_engine().begin() as db:
            assert db.execute(text("SELECT count(*) FROM app.recon_sources")).scalar_one() == 0
            set_org(db, UUID(organization))
            original = db.execute(text("SELECT original FROM app.recon_sources WHERE id=:id"), {"id": UUID(inputs[0])}).scalar_one()
            assert original == (ROOT / "sales.csv").read_bytes()
        inspection = create_engine(get_settings().migration_database_url, hide_parameters=True)
        try:
            with inspection.connect() as db:
                events = set(db.execute(text("""
                    SELECT event_type FROM app.audit_events WHERE org_id=:org AND event_type LIKE 'reconciliation.%'
                """), {"org": UUID(organization)}).scalars())
                assert {"reconciliation.source_uploaded", "reconciliation.mapping_versioned", "reconciliation.requested",
                        "reconciliation.completed", "reconciliation.exception_resolved",
                        "reconciliation.exception_acknowledged"}.issubset(events)
        finally:
            inspection.dispose()
