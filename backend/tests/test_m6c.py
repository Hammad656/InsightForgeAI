"""M6C approved widgets, audience isolation and scheduled report acceptance."""
import os
from datetime import datetime, timezone
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from app.core.database import get_engine
from app.core.tenant import set_org
from app.custom_dashboards import Widget
from app.jobs.imports import import_snapshot
from app.jobs.schedules import dispatch_schedules, prepare, wakeup
from app.main import app
from app.report_schedules import ScheduleConfig, next_occurrence, report_period
from test_m2 import HEADERS, org, register
from test_m3 import INVENTORY, SALES, payments_fixture, publish


@pytest.mark.parametrize("values", [
    {"metric": "arbitrary"}, {"kind": "chart"}, {"dimension": "branch"},
    {"filters": {"as_of": "2026-10-31"}}, {"filters": {"sql": "unapproved"}},
    {"filters": {"start": "2026-11-01", "end": "2026-10-01"}}, {"width": 3},
])
def test_widget_configuration_rejects_unsupported_values(values) -> None:
    with pytest.raises(ValidationError):
        Widget.model_validate({"id": uuid4(), "kind": "kpi", "metric": "net_revenue"} | values)


@pytest.mark.parametrize(("after", "hour", "minute", "expected"), [
    ("2026-03-08T05:00:00+00:00", 2, 30, "2026-03-08T07:30:00+00:00"),
    ("2026-11-01T04:00:00+00:00", 1, 30, "2026-11-01T05:30:00+00:00"),
    ("2026-11-01T06:00:00+00:00", 1, 30, "2026-11-02T06:30:00+00:00"),
])
def test_dst_schedules_choose_one_wall_clock_slot(after, hour, minute, expected) -> None:
    config = ScheduleConfig(name="Local", cadence="daily", timezone="America/New_York", hour=hour, minute=minute)
    assert next_occurrence(config, datetime.fromisoformat(after)).isoformat() == expected


def test_calendar_cadence_and_period_boundary() -> None:
    start = datetime(2026, 11, 1, 1, tzinfo=timezone.utc)
    weekly = ScheduleConfig(name="Weekly", cadence="weekly", weekday=0, period="previous_week")
    assert next_occurrence(weekly, start).isoformat() == "2026-11-02T04:00:00+00:00"
    monthly = ScheduleConfig(name="Monthly", cadence="monthly", period="previous_month")
    assert [day.isoformat() for day in report_period(monthly, next_occurrence(monthly, start))] == [
        "2026-10-01", "2026-11-01",
    ]


@pytest.mark.skipif(os.getenv("M6C_INTEGRATION") != "1", reason="requires disposable PostgreSQL and Redis")
def test_every_available_widget_metric_matches_m3(monkeypatch) -> None:
    monkeypatch.setattr(import_snapshot, "apply_async", lambda args, **kwargs: import_snapshot.run(*args))
    with TestClient(app, base_url="http://localhost", headers=HEADERS, client=("192.0.2.20", 50000)) as admin:
        register(admin)
        path = org(admin)
        scope = {"organization_id": path.rsplit("/", 1)[1]}
        catalog = admin.get(path + "/metrics").json()
        period = {"start": "2026-10-01", "end": "2026-11-01"}
        for domain, source, fixture, filters in (
            ("sales", "sales", SALES, period | {"branch": "B1", "customer": "C1"}),
            ("inventory", "inventory", INVENTORY, period | {"as_of": "2026-10-31", "sku": "S1", "threshold": "5"}),
            ("receivables", "payments", payments_fixture(), {"as_of": "2026-10-31", "customer": "C1"}),
        ):
            version = publish(admin, path, source, fixture)
            metrics = [item for item in catalog if item["domain"] == domain and item["sql"] is not None]
            widgets = [{"id": str(uuid4()), "metric": item["metric_id"], "filters": filters} | (
                {"kind": "table", "dimension": "ageing_bucket"} if item["metric_id"] == "ageing" else {"kind": "kpi"}
            ) for item in metrics]
            created = admin.post("/api/v1/dashboards", params=scope, json={"title": domain, "widgets": widgets})
            assert created.status_code == 201, created.text
            endpoint = "/api/v1/dashboards/" + created.json()["id"]
            reference = admin.get(path + "/analytics/" + domain, params=filters).json()["reports"][domain]
            refreshed = admin.post(endpoint + "/refresh", params=scope)
            assert refreshed.status_code == 200, (domain, refreshed.text)
            assert refreshed.headers["cache-control"] == "private, no-store"
            results = refreshed.json()["widgets"]
            assert len(results) == len(metrics)
            for item, result in zip(metrics, results, strict=True):
                key = item["metric_id"]
                expected = reference["ageing"] if key == "ageing" else [{key: reference["metrics"][key]}]
                assert result["rows"] == expected, key
                assert result["source"]["version"] == version
                assert result["metric_version"] == item["version"]


@pytest.mark.skipif(os.getenv("M6C_INTEGRATION") != "1", reason="requires disposable PostgreSQL and Redis")
def test_dashboards_sharing_snapshots_and_scheduled_private_pdf(monkeypatch) -> None:
    monkeypatch.setattr(import_snapshot, "apply_async", lambda args, **kwargs: import_snapshot.run(*args))
    with (
        TestClient(app, base_url="http://localhost", headers=HEADERS, client=("192.0.2.21", 50000)) as admin,
        TestClient(app, base_url="http://localhost", headers=HEADERS, client=("192.0.2.22", 50000)) as analyst,
        TestClient(app, base_url="http://localhost", headers=HEADERS, client=("192.0.2.23", 50000)) as viewer,
    ):
        register(admin)
        analyst_credentials, viewer_credentials = register(analyst), register(viewer)
        path, foreign_path = org(admin), org(viewer)
        org_id = UUID(path.rsplit("/", 1)[1])
        scope = {"organization_id": str(org_id)}
        for client, credentials, role in [(analyst, analyst_credentials, "analyst"),
                                          (viewer, viewer_credentials, "viewer")]:
            invite = admin.post(path + "/invitations", json={"email": credentials["email"], "role": role})
            assert client.post(path + "/invitations/accept", json=invite.json()).status_code == 204
        viewer_id = viewer.get("/api/v1/auth/me").json()["id"]
        sales_version = publish(admin, path, "sales", SALES)
        foreign_version = publish(viewer, foreign_path, "sales", SALES)
        dashboard_api = "/api/v1/dashboards"
        config = {"title": "Retail dashboard", "widgets": [{
            "id": str(uuid4()), "metric": "net_revenue", "kind": "chart", "dimension": "sku",
            "filters": {"start": "2026-10-01", "end": "2026-11-01", "branch": "B1"},
            "dataset_version": sales_version, "width": 2, "height": 2,
        }]}
        created = analyst.post(dashboard_api, params=scope, json=config)
        assert created.status_code == 201, created.text
        target = dashboard_api + "/" + created.json()["id"]
        preflight = analyst.options(target, headers={"Origin": "http://localhost:3000",
                                                     "Access-Control-Request-Method": "PUT"})
        assert preflight.status_code == 200, preflight.text
        assert viewer.get(target, params=scope).status_code == 404
        assert admin.get(target, params=scope).status_code == 404
        assert viewer.post(dashboard_api, params=scope, json=config).status_code == 403
        assert analyst.get(target, params=scope).json()["widgets"] == config["widgets"]
        updated = config | {"title": "Renamed dashboard", "revision": 1}
        assert analyst.put(target, params=scope, json=updated).json() == {"revision": 2}
        assert analyst.put(target, params=scope, json=updated).status_code == 409
        assert analyst.put(target + "/default", params=scope).status_code == 204
        assert analyst.get(dashboard_api, params=scope).json()["default_id"] == created.json()["id"]
        copy = analyst.post(target + "/duplicate", params=scope)
        assert copy.status_code == 201, copy.text
        assert analyst.delete(dashboard_api + "/" + copy.json()["id"], params=scope).status_code == 204
        foreign_widget = config["widgets"][0] | {"dataset_version": foreign_version}
        assert analyst.post(dashboard_api, params=scope, json=config | {"widgets": [foreign_widget]}).status_code == 404
        share = target + "/shares/" + viewer_id
        assert analyst.put(share, params=scope, json={"editor": True}).status_code == 422
        assert analyst.put(share, params=scope, json={"editor": False}).status_code == 204
        assert viewer.get(target, params=scope).json()["editable"] is False
        assert viewer.put(target, params=scope, json=updated).status_code == 403
        assert viewer.get(target, params={"organization_id": foreign_path.rsplit("/", 1)[1]}).status_code == 404
        publish(admin, path, "sales", "\n".join(SALES.splitlines()[:2]) + "\n")
        refreshed = viewer.post(target + "/refresh", params=scope)
        assert refreshed.status_code == 200, refreshed.text
        result = refreshed.json()["widgets"][0]
        assert result["rows"] == [{"sku": "S1", "net_revenue": "1000"}, {"sku": "S2", "net_revenue": "775"}]
        assert result["source"]["version"] == sales_version and result["source"]["active"] is False
        assert refreshed.headers["cache-control"] == "private, no-store"
        assert analyst.delete(share, params=scope).status_code == 204
        revoked = viewer.post(target + "/refresh", params=scope)
        assert revoked.status_code == 404 and revoked.headers["cache-control"] == "private, no-store"
        assert "1000" not in revoked.text and sales_version not in revoked.text
        with get_engine().begin() as db:
            set_org(db, UUID(foreign_path.rsplit("/", 1)[1]))
            assert db.execute(text("SELECT count(*) FROM app.dashboards WHERE org_id=:org"),
                              {"org": org_id}).scalar_one() == 0

        schedules = "/api/v1/report-schedules"
        scheduled = analyst.post(schedules, params=scope, json={"name": "Daily close", "cadence": "daily",
                                                                "period": "previous_month"})
        assert scheduled.status_code == 201, scheduled.text
        schedule = UUID(scheduled.json()["id"])
        schedule_path = schedules + "/" + str(schedule)
        assert viewer.get(schedule_path + "/runs", params=scope).status_code == 403
        assert admin.get(schedule_path + "/runs", params=scope).status_code == 404
        assert analyst.patch(schedule_path, params=scope, json={"paused": True}).json()["paused"] is True
        due = datetime(2026, 11, 5, 4, tzinfo=timezone.utc)
        with get_engine().begin() as db:
            set_org(db, org_id)
            db.execute(text("UPDATE app.report_schedules SET next_at=:due WHERE org_id=:org AND id=:id"),
                       {"due": due, "org": org_id, "id": schedule})
        assert prepare(org_id, schedule, datetime.now(timezone.utc)) == []
        assert analyst.get(schedule_path + "/runs", params=scope).json() == []
        assert analyst.patch(schedule_path, params=scope, json={"paused": False}).json()["paused"] is False
        with get_engine().begin() as db:
            set_org(db, org_id)
            db.execute(text("UPDATE app.report_schedules SET next_at=:due WHERE org_id=:org AND id=:id"),
                       {"due": due, "org": org_id, "id": schedule})
            wakeup(db, org_id, schedule, due)

        if os.getenv("M6C_LIVE_WORKER") == "1":
            dispatch_schedules.apply_async()
        else:
            dispatch_schedules.run()
        deadline = monotonic() + 30
        while monotonic() < deadline:
            runs = analyst.get(schedule_path + "/runs", params=scope).json()
            if runs and runs[0]["status"] not in {"queued", "processing"}:
                break
            sleep(.15)
        assert len(runs) == 1 and runs[0]["status"] == "completed", runs
        prepare(org_id, schedule, datetime.now(timezone.utc))
        assert len(analyst.get(schedule_path + "/runs", params=scope).json()) == 1
        report = "/api/v1/reports/" + runs[0]["report_id"]
        assert viewer.post(report + "/download-grant", params=scope).status_code == 404
        assert admin.post(report + "/download-grant", params=scope).status_code == 200
        grant = analyst.post(report + "/download-grant", params=scope).json()
        pdf = analyst.post(report + "/download", params=scope, json={"token": grant["token"]})
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        assert pdf.headers["cache-control"] == "private, no-store"
        assert analyst.post(report + "/download", params=scope, json={"token": grant["token"]}).status_code == 404
        assert viewer.post(report + "/download-grant", params={"organization_id": foreign_path.rsplit("/", 1)[1]}
                           ).status_code == 404
        assert any(notice["kind"] == "report" and notice["status"] == "completed"
                   for notice in analyst.get("/api/v1/notifications", params=scope).json())

        for cadence, period, day, expected_start in (
            ("weekly", "previous_week", 6, "2026-10-26"),
            ("monthly", "previous_month", 7, "2026-10-01"),
        ):
            edited = analyst.put(schedule_path, params=scope, json={
                "name": "Edited close", "cadence": cadence, "period": period, "weekday": 4, "month_day": 7,
            })
            assert edited.status_code == 200, edited.text
            assert edited.json()["config"]["cadence"] == cadence
            # Simulate broker delivery becoming unavailable after the job commits.
            with get_engine().begin() as db:
                set_org(db, org_id)
                db.execute(text("UPDATE app.report_schedules SET next_at=:due WHERE org_id=:org AND id=:id"),
                           {"due": datetime(2026, 11, day, 4, tzinfo=timezone.utc), "org": org_id, "id": schedule})
            now = datetime.now(timezone.utc)
            pending = prepare(org_id, schedule, now)
            assert len(pending) == 1
            assert prepare(org_id, schedule, now) == pending
            retry_runs = analyst.get(schedule_path + "/runs", params=scope).json()
            assert len(retry_runs) == day - 4 and retry_runs[0]["report_id"] == str(pending[0])
            with get_engine().begin() as db:
                set_org(db, org_id)
                wakeup(db, org_id, schedule, now)
            if os.getenv("M6C_LIVE_WORKER") == "1":
                dispatch_schedules.apply_async()
            else:
                dispatch_schedules.run()
            deadline = monotonic() + 30
            while monotonic() < deadline:
                retry_runs = analyst.get(schedule_path + "/runs", params=scope).json()
                if retry_runs[0]["status"] not in {"queued", "processing"}:
                    break
                sleep(.15)
            assert len(retry_runs) == day - 4 and retry_runs[0]["status"] == "completed", retry_runs
            metadata = analyst.get("/api/v1/reports/" + retry_runs[0]["report_id"], params=scope).json()
            assert metadata["filters"]["start"] == expected_start
