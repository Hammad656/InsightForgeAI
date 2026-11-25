import json
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core import telemetry
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
