import json
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core import telemetry
from app.api.v1 import observability
from app.core.queue_telemetry import head_age_ms
from app.main import app


def test_request_telemetry_ignores_untrusted_correlation_and_query_string(monkeypatch) -> None:
    records = []
    monkeypatch.setattr(telemetry.logger, "info", lambda message: records.append(json.loads(message)))
    with TestClient(app) as client:
        response = client.get("/api/v1/health?debug=synthetic-private-value", headers={"X-Correlation-ID": "untrusted"})
    assert response.status_code == 200
    trace = str(UUID(response.headers["X-Correlation-ID"]))
    assert records[-1]["correlation_id"] == trace
    assert records[-1]["status"] == 200
    assert records[-1]["duration_ms"] >= 0
    assert "synthetic-private-value" not in json.dumps(records)
    assert "untrusted" not in json.dumps(records)


def test_sql_timing_does_not_record_statement_or_parameters(monkeypatch) -> None:
    records = []
    monkeypatch.setattr(telemetry.logger, "info", lambda message: records.append(json.loads(message)))
    engine = telemetry.observe_database(create_engine("sqlite://"))
    token = telemetry.correlation.set("synthetic-trace")
    try:
        with engine.connect() as db:
            db.execute(text("SELECT :synthetic_column"), {"synthetic_column": "synthetic-value"})
    finally:
        telemetry.correlation.reset(token)
        engine.dispose()
    assert records[0]["event"] == "db.query"
    assert records[0]["correlation_id"] == "synthetic-trace"
    assert records[0]["duration_ms"] >= 0
    assert "synthetic-value" not in json.dumps(records)
    assert "SELECT" not in json.dumps(records)


def test_queue_age_omits_missing_or_invalid_timestamps() -> None:
    assert head_age_ms(b'{"headers":{"insightforge_published_at":98.5}}', 100) == 1500
    assert head_age_ms(b'{"headers":{"insightforge_published_at":101}}', 100) is None
    assert head_age_ms(b'{"headers":{"insightforge_published_at":true}}', 100) is None
    assert head_age_ms(b'{"headers":{}}', 100) is None
    assert head_age_ms(None, 100) is None


def test_browser_telemetry_accepts_only_small_allowlisted_events(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(observability, "emit", lambda name, **fields: seen.append((name, fields)))
    monkeypatch.setattr(observability, "limit_key", lambda *_: None)
    url = "/api/v1/telemetry/browser"
    headers = {"Origin": "http://localhost:3000", "X-CSRF": "1"}
    with TestClient(app) as client:
        assert client.post(url, json={"name": "LCP", "value": 123}).status_code == 403
        assert client.post(url, headers=headers, json={"name": "LCP", "value": 123}).status_code == 204
        assert client.post(url, headers=headers, json={"name": "runtime_error", "value": 1}).status_code == 204
        assert client.post(url, headers=headers, json={"name": "LCP", "value": 1, "message": "private"}).status_code == 422
        assert client.post(url, headers=headers, content="x" * 1025).status_code == 413
    assert seen == [
        ("browser.performance", {"metric": "LCP", "duration_ms": 123}),
        ("browser.error", {"kind": "runtime_error"}),
    ]
