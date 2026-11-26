"""Executive report financial, storage and authorization acceptance."""
from io import BytesIO
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from celery.exceptions import Retry
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import create_engine, text

from app.ai_provider import ProviderUnavailable
from app.api.v1 import reports
from app.core.config import get_settings
from app.executive_report import NarrativeFocus, narrative, render
from app.jobs.reports import generate_report
from app.main import app
from app.report_storage import LocalStorage, S3Storage, StorageError, get_storage
from test_m2 import HEADERS, org, register
from test_m3 import INVENTORY, SALES, payments_fixture, publish

ROOT = "/api/v1/reports"
FILTERS = {"start": "2026-10-01", "end": "2026-11-01", "as_of": "2026-10-31", "prior_start": "2026-08-31"}


def extracted(content: bytes) -> str:
    pdf = PdfReader(BytesIO(content))
    assert len(pdf.pages) >= 2
    assert pdf.metadata.title == "Monthly Business Performance"
    return "\n".join(page.extract_text() for page in pdf.pages)


def test_pdf_with_no_sources_and_untrusted_text(monkeypatch) -> None:
    def unavailable():
        raise ProviderUnavailable("fixture")

    monkeypatch.setattr("app.executive_report.get_provider", unavailable)
    pdf = extracted(render(uuid4(), "<b>Synthetic & Retail</b>", FILTERS, {}, {}))
    assert "<b>Synthetic & Retail</b>" in pdf
    assert "Narrative: Deterministic" in pdf and "No published snapshot" in pdf
    assert "Unavailable" in pdf and "Page 1" in pdf and "Evidence appendix" in pdf


def test_narrative_uses_only_supported_focus(monkeypatch) -> None:
    class Mock:
        def structured(self, schema, instructions, context):
            assert context == {"available_focus": ["stock", "coverage"]}
            assert schema is NarrativeFocus
            return NarrativeFocus(focus="stock"), 1

    monkeypatch.setattr("app.executive_report.get_provider", Mock)
    notes, mode = narrative({"inventory": {"low_stock": 2}})
    assert mode == "AI-assisted focus" and "2 low-stock positions" in notes[0]


def test_storage_backends_are_private_and_scoped(tmp_path, monkeypatch) -> None:
    org_id, other, artifact = uuid4(), uuid4(), uuid4()
    storage = LocalStorage(tmp_path)
    storage.put(org_id, artifact, b"synthetic")
    assert storage.get(org_id, artifact) == b"synthetic"
    with pytest.raises(FileNotFoundError):
        storage.get(other, artifact)
    storage.delete(org_id, artifact)
    with pytest.raises(FileNotFoundError):
        storage.get(org_id, artifact)
    with pytest.raises(StorageError, match="HTTPS"):
        S3Storage("private", "http://example.test")

    class Client:
        def put_object(self, **kwargs):
            assert kwargs == {
                "Bucket": "private", "Key": f"reports/{org_id.hex}/{artifact.hex}.pdf",
                "Body": b"synthetic", "ContentType": "application/pdf",
            }

        def get_object(self, **kwargs):
            assert kwargs["Key"] == f"reports/{org_id.hex}/{artifact.hex}.pdf"
            return {"Body": BytesIO(b"synthetic")}

        def delete_object(self, **kwargs):
            assert kwargs == {"Bucket": "private", "Key": f"reports/{org_id.hex}/{artifact.hex}.pdf"}

    monkeypatch.setattr("app.report_storage.boto3.client", lambda *args, **kwargs: Client())
    remote = S3Storage("private", "https://s3.example.test")
    remote.put(org_id, artifact, b"synthetic")
    assert remote.get(org_id, artifact) == b"synthetic"
    remote.delete(org_id, artifact)


@pytest.fixture
def workspace(monkeypatch):
    import os

    if os.getenv("M5_INTEGRATION") != "1":
        pytest.skip("M5_INTEGRATION=1 requires isolated migrated PostgreSQL and Redis")
    from app.jobs.imports import import_snapshot

    with (
        TestClient(app, base_url="http://localhost", headers=HEADERS) as first,
        TestClient(app, base_url="http://localhost", headers=HEADERS) as second,
    ):
        register(first)
        other = register(second)
        path, other_path = org(first), org(second)
        monkeypatch.setattr(import_snapshot, "apply_async", lambda args, **kwargs: import_snapshot.run(*args))
        versions = {
            "sales": publish(first, path, "sales", SALES),
            "inventory": publish(first, path, "inventory", INVENTORY),
            "payments": publish(first, path, "payments", payments_fixture()),
        }
        yield first, second, path, other_path, other, versions


def request_body(path: str) -> dict[str, str]:
    return {"organization_id": path.rsplit("/", 1)[1], "request_key": str(uuid4()),
            "start": FILTERS["start"], "end": FILTERS["end"], "as_of": FILTERS["as_of"]}


def wait_completed(client, target: str, scope: dict[str, str]) -> dict:
    deadline = monotonic() + 25
    while monotonic() < deadline:
        result = client.get(target, params=scope).json()
        if result["status"] not in {"queued", "processing"}:
            assert result["status"] == "completed", result
            return result
        sleep(.15)
    pytest.fail("The report worker did not finish")


def test_frozen_pdf_integrity_tenant_rbac_and_audit(workspace, monkeypatch) -> None:
    owner, second, path, other_path, credentials, versions = workspace
    body = request_body(path)
    scope = {"organization_id": body["organization_id"]}
    monkeypatch.setattr(reports, "enqueue", lambda *_: None)
    started = owner.post(ROOT, json=body)
    assert started.status_code == 202, started.text
    job = started.json()
    assert job["status"] == "queued" and job["retry_available"]
    assert owner.post(ROOT, json=body).json()["id"] == job["id"]
    assert owner.post(ROOT, json={**body, "branch": "B2"}).status_code == 409
    assert owner.post(ROOT, json={**body, "request_key": str(uuid4()), "query_id": str(uuid4())}).status_code == 404
    target = f"{ROOT}/{job['id']}"
    assert owner.post(target + "/download-grant", params=scope).status_code == 409
    assert second.get(ROOT, params=scope).status_code == 404
    assert second.get(target, params=scope).status_code == 404
    assert second.post(target + "/download-grant", params=scope).status_code == 404
    assert second.get(target, params={"organization_id": other_path.rsplit("/", 1)[1]}).status_code == 404
    invitation = owner.post(path + "/invitations", json={"email": credentials["email"], "role": "viewer"})
    assert second.post(path + "/invitations/accept", json=invitation.json()).status_code == 204
    assert second.post(ROOT, json={**body, "request_key": str(uuid4())}).status_code == 403
    assert second.get(ROOT, params=scope).json() == []
    assert second.post(target + "/download-grant", params=scope).status_code == 404
    member = second.get("/api/v1/auth/me").json()["id"]
    assert owner.patch(path + f"/members/{member}", json={"role": "analyst"}).status_code == 204
    other_request = second.post(ROOT, json={**body, "request_key": str(uuid4())})
    assert other_request.status_code == 202
    assert [item["id"] for item in second.get(ROOT, params=scope).json()] == [other_request.json()["id"]]

    assert owner.post(path + f"/datasets/versions/{versions['sales']}/archive").status_code == 204
    replacement = "\n".join(SALES.splitlines()[:2]) + "\n"
    assert publish(owner, path, "sales", replacement) != versions["sales"]
    generate_report.run(body["organization_id"], job["id"])
    generate_report.run(body["organization_id"], job["id"])
    current = wait_completed(owner, target, scope)
    assert {domain: source["version"] for domain, source in current["sources"].items()} == versions
    assert len(current["artifact_sha256"]) == 64 and current["artifact_size"] > 0

    grant = owner.post(target + "/download-grant", params=scope).json()
    assert second.post(target + "/download", params=scope, json={"token": grant["token"]}).status_code == 404
    storage = get_storage()
    tenant_id, report_id = UUID(scope["organization_id"]), UUID(job["id"])
    verified = storage.get(tenant_id, report_id)
    storage.put(tenant_id, report_id, verified[:-1] + b"x")
    assert owner.post(target + "/download", params=scope, json={"token": grant["token"]}).status_code == 503
    storage.put(tenant_id, report_id, verified)
    response = owner.post(target + "/download", params=scope, json={"token": grant["token"]})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert owner.post(target + "/download", params=scope, json={"token": grant["token"]}).status_code == 404
    pdf = extracted(response.content)
    for phrase in (
        "PKR 1,775.2344", "PKR 887.6172", "PKR 1,000.00", "77.5234%", "23", "PKR 700.00",
        "PKR 610.00", "2026-10-01", "2026-11-01", "2026-10-31", "Net revenue", "Order count", "Top products",
        "Branch performance", "Available stock", "Unavailable", "Sales performance", "Evidence appendix",
        "net_revenue (v1)", "Reservations are unavailable", *versions.values(),
    ):
        assert phrase in pdf, phrase
    grant = owner.post(target + "/download-grant", params=scope).json()
    inspection = create_engine(get_settings().migration_database_url, hide_parameters=True)
    try:
        with inspection.begin() as db:
            db.execute(text("""
                UPDATE app.report_downloads SET expires_at=now()-interval '1 second' WHERE report_id=:id
            """), {"id": job["id"]})
            events = db.execute(text("""
                SELECT event_type,correlation_id,actor_id FROM app.audit_events
                WHERE org_id=:org AND resource_id=:id
            """), {"id": job["id"], "org": body["organization_id"]}).all()
            named = [event.event_type for event in events]
            assert named.count("report.requested") == named.count("report.completed") == named.count("report.downloaded") == 1
            assert all(event.correlation_id and event.actor_id for event in events)
            assert db.execute(text("""
                SELECT count(*) FROM app.audit_events WHERE org_id=:org AND event_type='authorization.denied.403'
            """), {"org": body["organization_id"]}).scalar_one() >= 1
        assert owner.post(target + "/download", params=scope, json={"token": grant["token"]}).status_code == 404
        with inspection.begin() as db:
            db.execute(text("UPDATE app.reports SET expires_at=now()-interval '1 second' WHERE id=:id"), {"id": job["id"]})
        assert owner.post(target + "/download-grant", params=scope).status_code == 410
    finally:
        inspection.dispose()


def test_retry_recovers_storage_failure(workspace, monkeypatch) -> None:
    owner, _, path, _, _, _ = workspace
    monkeypatch.setattr(reports, "enqueue", lambda *_: None)
    body = request_body(path)
    item = owner.post(ROOT, json=body).json()
    scope = {"organization_id": body["organization_id"]}
    target = f"{ROOT}/{item['id']}"

    def unavailable():
        raise StorageError("synthetic")

    with monkeypatch.context() as patch:
        patch.setattr("app.jobs.reports.get_storage", unavailable)
        def retry(**_):
            raise Retry
        patch.setattr(generate_report, "retry", retry)
        with pytest.raises(Retry):
            generate_report.run(body["organization_id"], item["id"])
    queued = owner.get(target, params=scope).json()
    assert queued["status"] == "queued" and queued["error_code"] == "temporary_failure"
    assert owner.post(target + "/retry", params=scope).status_code == 202
    generate_report.run(body["organization_id"], item["id"])
    wait_completed(owner, target, scope)


def test_live_worker_report(workspace) -> None:
    import os

    if os.getenv("M5_LIVE_WORKER") != "1":
        pytest.skip("M5_LIVE_WORKER=1 requires a live Celery worker")
    owner, _, path, _, _, _ = workspace
    body = request_body(path)
    result = owner.post(ROOT, json=body)
    assert result.status_code == 202, result.text
    target = f"{ROOT}/{result.json()['id']}"
    scope = {"organization_id": body["organization_id"]}
    wait_completed(owner, target, scope)
    grant = owner.post(target + "/download-grant", params=scope).json()
    artifact = owner.post(target + "/download", params=scope, json={"token": grant["token"]})
    assert "PKR 1,775.2344" in extracted(artifact.content)
