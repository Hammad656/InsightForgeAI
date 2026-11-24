"""Independent synthetic time-series arithmetic and live forecast acceptance."""
import os
from datetime import date, datetime
from time import monotonic, sleep
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import get_engine
from app.core.tenant import set_org
from app.jobs.imports import import_snapshot
from app.main import app
from app.orchestration import Task, task_result
from app.predictive import WEEK, ForecastSpec, aligned, anomalies, error_metrics, fit, forecast, monday, stockout
from m6b_worker import FORECAST, task
from test_m2 import HEADERS, org, register
from test_m3 import INVENTORY, publish


def series(end: date, count: int = 96) -> tuple[list[date], list[float]]:
    return ([end - (count - i) * WEEK for i in range(count)],
            [float(20 + 2 * i + (0, 7, -3, 4)[i % 4]) for i in range(count)])


def sales_fixture(end: date) -> str:
    days, values = series(end)
    return ("line_id,order_id,customer_id,sku,branch_id,ordered_on,quantity,unit_price,discount,unit_cost,status,currency\n"
            + "".join(f"L{i},O{i},C1,S1,B1,{day},{int(value)},10,0,4,completed,PKR\n"
                      for i, (day, value) in enumerate(zip(days, values, strict=True))))


def test_rolling_tuning_calibration_holdout_and_baseline_errors() -> None:
    days, values = series(date(2026, 11, 23))
    spec = ForecastSpec()
    result = fit(days, values, spec)
    assert result == fit(days, values, spec)
    assert result["model"] == "seasonal_trend"
    benchmarks = result["backtest"]
    assert benchmarks["seasonal_trend"] == {"n": 16, "mae": 0, "wape": 0, "bias": 0}
    assert benchmarks["seasonal_naive"]["mae"] == 8
    assert result["interval"]["hits"] == result["interval"]["samples"] == 16
    assert [row["point"] for row in result["forecast"]] == [212, 221, 213, 222]
    assert forecast(values[:64], 4, 4, "seasonal_naive", True) == values[60:64]
    perturbed = values[:-16] + [value + 100 for value in values[-16:]]
    shifted = fit(days, perturbed, spec)
    assert shifted["model"] == result["model"]
    assert shifted["artifact"]["widths"] == result["artifact"]["widths"]
    assert shifted["backtest"]["seasonal_trend"]["mae"] > 0


@pytest.mark.parametrize("horizon", [1, 2, 4])
def test_horizons_sparse_history_and_zero_denominator(horizon) -> None:
    days, values = series(date(2026, 11, 23))
    result = fit(days, [0.] * len(values), ForecastSpec(horizon=horizon))
    assert result["interval"]["samples"] == horizon * 4
    assert result["backtest"]["seasonal_naive"]["wape"] is None
    assert fit(days[:6], values[:6], ForecastSpec(horizon=horizon))["status"] == "insufficient_history"
    assert error_metrics([2., 4.], [1., 6.]) == {"n": 2, "mae": 1.5, "wape": .5, "bias": .5}


def test_alignment_anomaly_and_stockout_limits() -> None:
    end = date(2026, 11, 23)
    dates = [end - i * WEEK for i in (3, 2, 1)]
    scope = {dates[0]: 5., dates[2]: 3.}
    assert aligned(set(dates), scope, end, True)[1] == [5., 0., 3.]
    assert aligned(set(dates), scope, end, False)[1] == [3.]
    assert aligned({dates[0], dates[2]}, scope, end, True)[2] == 1
    dates, _ = series(end)
    observed = {day: 10. for day in dates}
    observed[dates[-1]] = 50.
    assert anomalies(observed, 4)[0]["score"] == 40
    estimate = {"forecast": [{"point": 70., "lower": 56., "upper": 84.}]}
    spec = ForecastSpec(sku="S1", branch="B1", lead_days=7, horizon=1)
    risk = stockout(estimate, spec, 60, end, 10)
    assert risk["risk"] == "estimated_shortfall" and risk["confirmed_replenishment"] is None
    assert risk["coverage_days"] == 6 and risk["demand"] == 70
    assert stockout(estimate, spec.model_copy(update={"lead_days": None}), 60, end, 10)["status"] == "unavailable"
    assert stockout(estimate, spec, None, end, 10)["status"] == "unavailable"
    supplied = ForecastSpec(sku="S1", branch="B1", lead_days=7, horizon=1,
                            replenishments=[{"due_on": end, "units": 25.}])
    assert stockout(estimate, supplied, 60, end, 10)["risk"] == "within_estimated_stock"


def test_noisy_fixture_reports_every_baseline_and_calibrated_intervals() -> None:
    days, values = series(date(2026, 11, 23))
    noise = (3, -4, 2, 1, -5, 5, 0, -1, 2)
    actual = [value + noise[i % len(noise)] for i, value in enumerate(values)]
    result = fit(days, actual, ForecastSpec())
    assert result["model"] == "naive"
    assert result["backtest"]["naive"]["mae"] == 5.6875
    assert result["backtest"]["seasonal_naive"]["mae"] == 8.25
    assert result["backtest"]["seasonal_trend"]["mae"] == 22.71875
    assert result["interval"]["hits"] == 16
    assert all(item["lower"] <= item["point"] <= item["upper"] for item in result["forecast"])
    actual_holdout = actual[-16:]
    for method in ("naive", "seasonal_naive"):
        predicted = []
        for origin in (80, 84, 88, 92):
            predicted.extend([actual[origin - 1]] * 4 if method == "naive" else actual[origin - 4:origin])
        total_error = sum(abs(value - prediction) for value, prediction in zip(actual_holdout, predicted, strict=True))
        measured = result["backtest"][method]
        assert measured["mae"] == total_error / 16
        assert measured["wape"] == total_error / sum(abs(value) for value in actual_holdout)
    widths = result["artifact"]["widths"]
    assert widths == [max(abs(actual[i] - actual[origin - 1]) for origin in range(48, 80, 4)
                          for i in [origin + lead]) for lead in range(4)]
    hits = sum(abs(actual[i] - actual[origin - 1]) <= widths[lead]
               for origin in (80, 84, 88, 92) for lead, i in enumerate(range(origin, origin + 4)))
    assert result["interval"]["coverage"] == hits / 16


def test_anomaly_threshold_and_history_controls() -> None:
    dates, _ = series(date(2026, 11, 23))
    observed = {day: 10. for day in dates}
    assert anomalies(observed, 4) == []
    assert anomalies({day: 100. if index == 11 else 10. for index, day in enumerate(dates[:12])}, 4) == []
    observed[dates[-1]] = 13.49
    assert anomalies(observed, 4) == []
    observed[dates[-1]] = 13.5
    assert anomalies(observed, 4)[0]["severity"] == "review"


@pytest.mark.skipif(os.getenv("M7_INTEGRATION") != "1", reason="requires isolated PostgreSQL and real Celery")
def test_live_forecast_lifecycle_with_rls_and_orchestration(monkeypatch) -> None:
    with (TestClient(app, base_url="http://localhost", headers=HEADERS, client=("192.0.2.71", 50000)) as admin,
          TestClient(app, base_url="http://localhost", headers=HEADERS, client=("192.0.2.72", 50000)) as external):
        register(admin)
        credentials = register(external)
        path, other = org(admin), org(external)
        identity = path.rsplit("/", 1)[1]
        scope = {"organization_id": identity}
        today = datetime.now(ZoneInfo("Asia/Karachi")).date()
        end = monday(today)
        monkeypatch.setattr(import_snapshot, "apply_async", lambda args, **kwargs: import_snapshot.run(*args))
        fixture = sales_fixture(end) + f"future,ofuture,C1,S1,B1,{end},999999,10,0,4,completed,PKR\n"
        version = publish(admin, path, "sales", fixture)
        inventory = publish(admin, path, "inventory", INVENTORY)
        payload = scope | {"request_key": str(uuid4()), "horizon": 4}
        started = admin.post("/api/v1/forecasts", json=payload)
        assert started.status_code == 202, started.text
        run = started.json()["id"]
        assert admin.post("/api/v1/forecasts", json=payload).json()["id"] == run
        endpoint = "/api/v1/forecasts/" + run
        cutoff = monotonic() + 30
        while monotonic() < cutoff:
            response = admin.get(endpoint, params=scope)
            record = response.json()
            if record["status"] not in {"queued", "running"}:
                break
            sleep(.2)
        assert record["status"] == "completed", record
        assert record["result"]["forecast"][0]["point"] == 212
        assert record["sources"]["sales"]["version"] == version
        assert "artifact" not in record and response.headers["cache-control"] == "private, no-store"
        model = admin.get(endpoint + "/model", params=scope).json()
        assert model["model_version"] == "weekly-seasonal-trend-v1" and len(model["training_digest"]) == 64
        with get_engine().begin() as db:
            set_org(db, UUID(identity))
            saved = db.execute(text("SELECT artifact FROM app.forecast_runs WHERE id=:id"),
                               {"id": UUID(run)}).scalar_one()
            assert saved["training_digest"] == model["training_digest"]
            assert saved["training_weeks"] == 96 and saved["parameters"]["slope_per_week"] == 2
        assert external.get(endpoint, params=scope).status_code == 404
        assert external.get(endpoint, params={"organization_id": other.rsplit("/", 1)[1]}).status_code == 404
        planned = task(1, "forecast", None)
        result = task_result(Task.model_validate(planned), UUID(identity), record["sources"]["sales"], today, lambda: 2000)
        assert result["status"] == "completed" and str(result["evidence"]["forecast_id"]) == run
        assert task_result(Task.model_validate(planned), UUID(other.rsplit("/", 1)[1]),
                           record["sources"]["sales"], today, lambda: 2000)["status"] == "unavailable"
        submitted = admin.post("/api/v1/ai/analyses", json=scope | {
            "question": FORECAST, "request_key": str(uuid4()),
        })
        assert submitted.status_code == 202, submitted.text
        analysis_path = "/api/v1/ai/analyses/" + submitted.json()["id"]
        cutoff = monotonic() + 30
        while monotonic() < cutoff:
            analysis = admin.get(analysis_path, params=scope).json()
            if analysis["status"] not in {"queued", "running"}:
                break
            sleep(.2)
        assert analysis["tasks"]["4"]["status"] == "completed", analysis
        assert analysis["tasks"]["4"]["evidence"]["forecast_id"] == run
        risk_request = admin.post("/api/v1/forecasts", json=scope | {
            "request_key": str(uuid4()), "sku": "S1", "branch": "B1", "lead_days": 7,
        })
        assert risk_request.status_code == 202, risk_request.text
        risk_path = "/api/v1/forecasts/" + risk_request.json()["id"]
        cutoff = monotonic() + 30
        while monotonic() < cutoff:
            risk_record = admin.get(risk_path, params=scope).json()
            if risk_record["status"] not in {"queued", "running"}:
                break
            sleep(.2)
        assert risk_record["status"] == "completed", risk_record
        assert risk_record["sources"]["inventory"]["version"] == inventory
        risk = admin.get(risk_path + "/stockout", params=scope).json()["stockout"]
        assert risk["risk"] == "estimated_shortfall" and risk["stock_on_hand"] == 20
        assert risk["demand"] == pytest.approx(212) and risk["confirmed_replenishment"] is None
        invitation = admin.post(path + "/invitations", json={"role": "viewer", "email": credentials["email"]})
        assert external.post(path + "/invitations/accept", json=invitation.json()).status_code == 204
        assert external.get(endpoint, params=scope).status_code == 200
        assert external.post("/api/v1/forecasts", json=payload).status_code == 403
