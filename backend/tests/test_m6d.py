"""M6D held-out goldens and persistent evaluation contracts."""
import json
import os
from dataclasses import replace
from decimal import Decimal
from time import monotonic, sleep
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import RowMapping, text

from app import evaluation
from app.ai_provider import OpenAIProvider
from app.api.v1 import evaluations
from app.api.v1.evaluations import comparison
from app.core.database import get_engine
from app.core.tenant import set_org
from app.eval_data import seed
from app.eval_fixtures import CASES, OCT
from app.eval_reference import inventory, receivables, sales
from app.evaluation import Replay, manifest, run_case, summarize
from app.main import app
from test_m2 import HEADERS, org, register


def test_fixture_contract_and_independent_known_totals() -> None:
    assert len(CASES) == 162
    assert len({case.id for case in CASES}) == len({case.question for case in CASES}) == len(CASES)
    assert len({(case.domain, case.metric, str(case.options)) for case in CASES}) == len(CASES)
    assert sales("net_revenue", OCT) == [{"net_revenue": Decimal("490.020")}]
    assert sales("cogs", OCT) == [{"cogs": None}]
    assert sales("order_count", OCT) == [{"order_count": 3}]
    assert inventory("stock_on_hand", {"as_of": "2026-11-01"}) == [{"stock_on_hand": Decimal(11)}]
    assert receivables("outstanding", {"as_of": "2026-10-31"}) == [{"outstanding": Decimal(325)}]
    assert sales("sales_growth", OCT) == [{"sales_growth": Decimal("99.1951")}]
    assert sales("average_order_value", OCT | {"branch": "Depot"}) == [{"average_order_value": None}]
    assert inventory("low_stock", {"as_of": "2026-10-31"}) == [{"low_stock": 3}]
    assert [row["balance"] for row in receivables("ageing", {"as_of": "2026-10-31"})] == [155, 80, 0, 0, 90]
    assert manifest()["case_count"] == len(CASES)


def test_scoring_excludes_only_inapplicable_fields() -> None:
    sample: dict[str, Any] = dict.fromkeys(("execution_valid", "result_correct", "metric_correct", "faithful",
                                           "decision_correct", "tenant_isolated", "sql_denied", "tokens",
                                           "estimated_cost_usd", "no_unsupported_claim"))
    score = summarize([
        sample | {"domain": "sales", "passed": False, "result_correct": False, "latency_ms": 3},
        sample | {"domain": "security", "passed": True, "decision_correct": True, "latency_ms": 19},
    ])
    assert score["result_correct"] == {"passed": 0, "total": 1}
    assert score["decision_correct"] == {"passed": 1, "total": 1}
    assert score["tenant_isolated"]["total"] == 0
    assert score["latency_p50_ms"] == 3 and score["latency_p95_ms"] == 19
    assert score["estimated_cost_usd"] is None and score["token_samples"] == 0


def test_regression_comparison_carries_model_and_prompt() -> None:
    def row(model: str, prompt: str, outcomes: dict[str, bool]) -> RowMapping:
        return cast(RowMapping, {"model": model, "status": "completed", "manifest": {
            "fixture_hash": "same", "prompt_version": prompt, },
            "results": [{"id": key, "passed": result} for key, result in outcomes.items()]})
    value = comparison(row("new", "v2", {"A": False, "B": True}), row("old", "v1", {"A": True, "B": False, "C": True}))
    assert value["paired"] == 2 and value["regressions"] == ["A"] and value["improvements"] == ["B"]
    assert value["current_prompt"] == "v2" and value["baseline_model"] == "old"


def test_live_mode_cap_and_estimate_without_provider_calls(monkeypatch) -> None:
    from app.core.config import Settings

    provider = OpenAIProvider("fixture", "test-model")
    monkeypatch.setattr(evaluation, "live_available", lambda: True)
    monkeypatch.setattr(evaluation, "get_provider", lambda: provider)
    monkeypatch.setattr(evaluation, "get_settings", lambda: Settings(evaluation_cost_ceiling_per_million_tokens=Decimal("3")))
    monkeypatch.setattr(evaluation, "seed", dict)
    visited = []

    def evaluate_case(case, sources, selected):
        assert selected is provider and sources == {}
        visited.append(case.id)
        return dict.fromkeys(("execution_valid", "result_correct", "metric_correct", "faithful", "decision_correct",
                              "tenant_isolated", "sql_denied", "no_unsupported_claim")) | {
            "passed": True, "domain": case.domain, "latency_ms": 1, "tokens": 1000,
        }

    monkeypatch.setattr(evaluation, "run_case", evaluate_case)
    results, scores = evaluation.execute("live_provider", "test-model")
    assert set(visited) == evaluation.LIVE_CASE_IDS and len(visited) == 6
    assert all(item["estimated_cost_usd"] == .003 for item in results)
    assert scores["estimated_cost_usd"] == pytest.approx(.018)


@pytest.mark.skipif(os.getenv("M6D_INTEGRATION") != "1", reason="requires disposable PostgreSQL and Redis")
def test_live_worker_benchmark_history_export_and_isolation(monkeypatch) -> None:
    path = "/api/v1/evaluations/runs"
    with (TestClient(app, base_url="http://localhost", headers=HEADERS, client=("192.0.2.41", 50000)) as admin,
          TestClient(app, base_url="http://localhost", headers=HEADERS, client=("192.0.2.42", 50000)) as other):
        register(admin)
        credentials = register(other)
        base, other_base = org(admin), org(other)
        owner_scope = {"organization_id": base.rsplit("/", 1)[1]}
        foreign_scope = {"organization_id": other_base.rsplit("/", 1)[1]}
        body = {"request_key": str(uuid4())}
        assert admin.post(path, params=owner_scope, json=body | {"live": True, "approve_provider_cost": True}
                          ).status_code == 403
        started = admin.post(path, params=owner_scope, json=body)
        assert started.status_code == 202, started.text
        run = started.json()["id"]
        assert admin.post(path, params=owner_scope, json=body).json()["id"] == run
        target = path + "/" + run
        deadline = monotonic() + 60
        while monotonic() < deadline:
            value = admin.get(target, params=owner_scope).json()
            if value["status"] not in {"queued", "running"}:
                break
            sleep(.1)
        assert value["status"] == "completed", value
        assert value["scores"]["passed"] == {"passed": 162, "total": 162}, [
            item for item in value["results"] if not item["passed"]
        ]
        assert value["scores"]["tenant_isolated"] == {"passed": 1, "total": 1}
        assert value["scores"]["sql_denied"] == {"passed": 4, "total": 4}
        assert value["scores"]["decision_correct"] == {"passed": 12, "total": 12}
        assert admin.put(target + "/baseline", params=owner_scope).status_code == 204
        assert admin.get(path, params=owner_scope).json()["baseline_id"] == run
        result = admin.get(target + "/compare", params=owner_scope | {"baseline_id": run}).json()
        assert result["paired"] == 162 and result["regressions"] == []
        exported = admin.get(target + "/export", params=owner_scope)
        assert exported.status_code == 200 and exported.headers["cache-control"] == "private, no-store"
        assert exported.headers["content-disposition"].startswith("attachment")
        assert all("question" not in item and "answer" not in item and "rows" not in item
                   for item in exported.json()["results"])

        # The queue can be temporarily unavailable after the run is committed.
        with monkeypatch.context() as patch:
            patch.setattr(evaluations, "enqueue", lambda *_: None)
            deferred = admin.post(path, params=owner_scope, json={"request_key": str(uuid4())}).json()
        assert deferred["status"] == "queued"
        deferred_path = path + "/" + deferred["id"]
        assert admin.post(deferred_path + "/retry", params=owner_scope).status_code == 202
        deadline = monotonic() + 60
        while monotonic() < deadline:
            recovered = admin.get(deferred_path, params=owner_scope).json()
            if recovered["status"] not in {"queued", "running"}:
                break
            sleep(.1)
        assert recovered["status"] == "completed", recovered
        compared = admin.get(deferred_path + "/compare", params=owner_scope | {"baseline_id": run}).json()
        assert compared["paired"] == 162 and compared["regressions"] == [] and compared["improvements"] == []

        # Score an intentionally wrong candidate intent through the reporting path.
        case = CASES[0]
        regressed = run_case(case, seed(), Replay(replace(case, metric="order_count")))
        assert regressed["execution_valid"] and not regressed["passed"]
        assert regressed["failure"] == "answer_mismatch"
        results = [regressed if item["id"] == case.id else item for item in recovered["results"]]
        with get_engine().begin() as db:
            set_org(db, UUID(owner_scope["organization_id"]))
            db.execute(text("""
                UPDATE app.evaluation_runs SET model='synthetic-regression',results=CAST(:results AS jsonb),
                    scores=CAST(:scores AS jsonb) WHERE id=:id
            """), {"id": UUID(deferred["id"]), "results": json.dumps(results),
                   "scores": json.dumps(summarize(results))})
        comparison_result = admin.get(deferred_path + "/compare", params=owner_scope | {"baseline_id": run}).json()
        assert comparison_result["paired"] == 162 and comparison_result["regressions"] == [case.id]
        assert comparison_result["baseline_model"] != comparison_result["current_model"]
        assert admin.put(deferred_path + "/baseline", params=owner_scope).status_code == 204
        reverse = admin.get(target + "/compare", params=owner_scope | {"baseline_id": deferred["id"]}).json()
        assert reverse["improvements"] == [case.id] and reverse["regressions"] == []
        assert other.get(target + "/export", params=foreign_scope).status_code == 404
        assert other.get(target, params=owner_scope).status_code == 404
        invite = admin.post(base + "/invitations", json={"email": credentials["email"], "role": "viewer"})
        assert other.post(base + "/invitations/accept", json=invite.json()).status_code == 204
        assert other.get(target, params=owner_scope).status_code == 403
        assert other.post(path, params=owner_scope, json={"request_key": str(uuid4())}).status_code == 403
        with get_engine().begin() as db:
            set_org(db, UUID(foreign_scope["organization_id"]))
            count = db.execute(text("SELECT count(*) FROM app.evaluation_runs WHERE id=:id"), {"id": UUID(run)})
            assert count.scalar_one() == 0
