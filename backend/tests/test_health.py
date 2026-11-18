from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_liveness() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_fails_safely_when_database_unavailable(monkeypatch) -> None:
    def unavailable():
        raise RuntimeError("sensitive details")

    monkeypatch.setattr("app.api.v1.health.get_engine", unavailable)
    response = client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
