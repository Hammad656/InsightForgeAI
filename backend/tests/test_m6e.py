"""Destination, privilege, tenant and atomic sync acceptance checks."""
import os
import socket
from time import monotonic, sleep
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.core.database import get_engine
from app.core.tenant import set_org
from app.ingestion import FIELDS
from app.main import app
from app.pg_connector import ConnectorError, Profile, password, pinned_address, profile, remote
from m6e_source import ORG, owner
from test_m2 import HEADERS, org, register


@pytest.mark.parametrize("address", ["127.0.0.1", "10.2.3.4", "169.254.169.254", "100.100.100.200", "224.0.0.1",
                                     "::1", "fd00::1", "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe"])
def test_denied_destination(monkeypatch, address) -> None:
    monkeypatch.setattr("app.pg_connector.get_settings", lambda: Settings())
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [(0, 0, 0, "", (address, 5432))])
    item = Profile(host="approved.example", port=5432, address=address, database="ledger",
                   username="reader", secret_ref="reader", objects=[{"schema_name": "feed", "table": "sales"}])
    with pytest.raises(ConnectorError, match="destination_denied"):
        pinned_address(item)


def test_dns_change_and_tls_requirement(monkeypatch, tmp_path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("fixture", encoding="ascii")
    item = Profile(host="approved.example", port=5432, address="8.8.8.8", database="ledger", username="reader",
                   secret_ref="reader", ca_file=ca, objects=[{"schema_name": "feed", "table": "sales"}])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [(0, 0, 0, "", ("8.8.8.8", 5432))])
    assert pinned_address(item) == "8.8.8.8"
    with pytest.raises(ConnectorError, match="tls_configuration_required"):
        pinned_address(item.model_copy(update={"ca_file": None}))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [
        (0, 0, 0, "", ("8.8.8.8", 5432)), (0, 0, 0, "", ("127.0.0.1", 5432)),
    ])
    with pytest.raises(ConnectorError, match="destination_denied"):
        pinned_address(item)


def test_secret_files_are_bounded_and_read_again_on_rotation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.pg_connector.get_settings", lambda: Settings(pg_connector_secret_dir=tmp_path))
    item = Profile(host="approved.example", port=5432, address="8.8.8.8", database="ledger",
                   username="reader", secret_ref="reader", objects=[{"schema_name": "feed", "table": "sales"}])
    credential = tmp_path / "reader"
    credential.write_text("first-fixture\n", encoding="ascii")
    assert password(item) == "first-fixture"
    credential.write_text("rotated-fixture\n", encoding="ascii")
    assert password(item) == "rotated-fixture"
    credential.write_text("x" * 4096 + "\n" * 10, encoding="ascii")
    with pytest.raises(ConnectorError, match="^secret_unavailable$"):
        password(item)


@pytest.mark.skipif(os.getenv("M6E_INTEGRATION") != "1", reason="requires the isolated connector configuration")
def test_connection_refusal_and_database_read_only_transaction() -> None:
    item = profile(ORG, "approved")
    with socket.socket() as blocked:
        blocked.bind(("127.0.0.1", 0))
        with pytest.raises(ConnectorError, match="^source_connection_failed$"):
            with remote(item.model_copy(update={"port": blocked.getsockname()[1]})):
                pytest.fail("Unexpected connection")
    with remote(item) as conn:
        assert conn.execute("SHOW transaction_read_only").fetchone() == ("on",)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("UPDATE ONLY feed.sales SET status='pending' WHERE false")


@pytest.mark.skipif(os.getenv("M6E_INTEGRATION") != "1", reason="requires separate PostgreSQL source and live worker")
def test_external_connector_discovery_and_atomic_publication() -> None:
    path = f"/api/v1/organizations/{ORG}/connectors"
    with (TestClient(app, base_url="http://localhost", headers=HEADERS, client=("192.0.2.51", 50000)) as admin,
          TestClient(app, base_url="http://localhost", headers=HEADERS, client=("192.0.2.52", 50000)) as other):
        register(admin)
        credentials = register(other)
        other_path = org(other)
        actor = UUID(admin.get("/api/v1/auth/me").json()["id"])
        with get_engine().begin() as db:
            set_org(db, ORG)
            db.execute(text("INSERT INTO app.organizations(id,name) VALUES (:org,'External fixture') ON CONFLICT DO NOTHING"),
                       {"org": ORG})
            db.execute(text("INSERT INTO app.memberships VALUES (:org,:actor,'admin')"), {"org": ORG, "actor": actor})
        settings = admin.get(path + "/profiles")
        assert settings.status_code == 200 and "synthetic_reader_fixture" not in settings.text
        assert all(set(item) == {"key", "host", "port", "database", "security"} for item in settings.json()["profiles"])
        assert admin.post(path, json={"label": "Unsafe", "profile": "approved", "port": 5433}).status_code == 422
        assert admin.post(path, json={"label": "Unsafe", "profile": "unknown"}).status_code == 422
        created = admin.post(path, json={"label": "External sales", "profile": "approved"})
        assert created.status_code == 201, created.text
        target = path + "/" + created.json()["id"]
        assert other.post(target + "/test").status_code == 404
        foreign_target = other_path + "/connectors/" + created.json()["id"]
        assert other.post(foreign_target + "/test").status_code == 404
        invite = admin.post(path.removesuffix("/connectors") + "/invitations",
                            json={"role": "viewer", "email": credentials["email"]})
        assert other.post(path.removesuffix("/connectors") + "/invitations/accept", json=invite.json()).status_code == 204
        assert other.get(path).status_code == 403

        wrong = admin.post(path, json={"label": "Wrong authentication", "profile": "bad-auth"}).json()["id"]
        denied = admin.post(path + "/" + wrong + "/test")
        assert denied.status_code == 503 and denied.json()["detail"] == "source_connection_failed"
        if os.getenv("M6E_SOURCE_CA"):
            bad_tls = admin.post(path, json={"label": "Untrusted TLS", "profile": "bad-tls"}).json()["id"]
            rejected = admin.post(path + "/" + bad_tls + "/test")
            assert rejected.status_code == 503 and rejected.json()["detail"] == "source_connection_failed"
        if os.getenv("M6E_TRUSTED_SOURCE_CA"):
            trusted_tls = admin.post(path, json={"label": "Trusted TLS", "profile": "trusted-tls"}).json()["id"]
            verified = admin.post(path + "/" + trusted_tls + "/test")
            assert verified.status_code == 200, verified.text
        approved = admin.post(target + "/test")
        assert approved.status_code == 200, approved.text
        assert len(approved.json()["objects"]) == 2
        obj = {"schema_name": "feed", "table": "sales"}
        found = admin.post(target + "/columns", json=obj)
        assert found.status_code == 200, found.text
        assert {item["name"] for item in found.json()} == set(FIELDS["sales"])
        denied = admin.post(target + "/columns", json=obj | {"table": "private_feed"})
        assert denied.json()["detail"] == "source_object_denied"
        assert admin.post(target + "/columns", json=obj | {"sql": "SELECT 1"}).status_code == 422
        assert admin.post(target + "/columns", json={"schema_name": "pg_catalog", "table": "pg_class"}).status_code == 422
        with owner() as remote:
            remote.execute("GRANT UPDATE(status) ON feed.sales TO connector_reader")
        try:
            rejected = admin.post(target + "/test")
            assert rejected.json()["detail"] == "unsafe_source_role", rejected.text
        finally:
            with owner() as remote:
                remote.execute("REVOKE UPDATE(status) ON feed.sales FROM connector_reader")
        body = obj | {"domain": "sales", "mapping": {field: field for field in FIELDS["sales"]}}
        assert admin.post(target + "/preview", json=body | {"source_options": {"sql": "SELECT 1"}}).status_code == 422
        bad_map = body | {"mapping": body["mapping"] | {"sku": "restricted_value"}}
        assert admin.post(target + "/preview", json=bad_map).json()["detail"] == "source_column_denied"
        preview = admin.post(target + "/preview", json=body)
        assert preview.status_code == 200 and len(preview.json()["rows"]) == 1

        def sync(payload, key=None):
            response = admin.post(target + "/syncs", json=payload | {"request_key": key or str(uuid4())})
            assert response.status_code == 202, response.text
            return response.json()["id"]

        def wait(job):
            deadline = monotonic() + 25
            while monotonic() < deadline:
                result = next(item for item in admin.get(target + "/syncs").json() if item["id"] == job)
                if result["status"] not in {"queued", "running"}:
                    return result
                sleep(.15)
            pytest.fail("Sync did not reach a terminal state")

        key = str(uuid4())
        first = sync(body, key)
        assert sync(body, key) == first
        done = wait(first)
        assert done["status"] == "completed" and done["row_count"] == 1, done
        assert wait(sync(body))["version_id"] == done["version_id"]
        result = admin.get(path.removesuffix("/connectors") + "/analytics/sales", params={
            "start": "2026-10-01", "end": "2026-11-01",
        }).json()["reports"]["sales"]
        assert result["metrics"]["net_revenue"] == "49"
        with get_engine().begin() as db:
            set_org(db, ORG)
            provenance = db.execute(text("""
                SELECT s.connector_id,s.config,s.version_id,u.digest
                FROM app.pg_syncs s JOIN app.import_jobs j ON (j.org_id,j.id)=(s.org_id,s.version_id)
                JOIN app.uploads u ON (u.org_id,u.id)=(j.org_id,j.upload_id)
                WHERE s.org_id=:org AND s.id=:id
            """), {"org": ORG, "id": UUID(first)}).mappings().one()
            assert str(provenance["connector_id"]) == created.json()["id"]
            assert str(provenance["version_id"]) == done["version_id"] and len(provenance["digest"]) == 64
            assert provenance["config"]["mapping"] == body["mapping"]
        with owner() as remote:
            remote.execute("UPDATE feed.sales SET status='invalid'")
        try:
            invalid = wait(sync(body))
            assert invalid["error_code"] == "invalid_source_data"
            active = admin.get(path.removesuffix("/connectors") + "/datasets").json()
            assert next(item for item in active if item["domain"] == "sales")["active_version"] == done["version_id"]
        finally:
            with owner() as remote:
                remote.execute("UPDATE feed.sales SET status='completed'")
        retry = admin.post(target + "/syncs/" + invalid["id"] + "/retry")
        assert retry.status_code == 202
        assert wait(invalid["id"])["version_id"] == done["version_id"]
        keys = ["transaction_id", "customer_id", "product_id", "branch_id", "invoice_id", "date", "amount"]
        recon = wait(sync({"schema_name": "feed", "table": "reconciliation", "domain": "sales",
                           "purpose": "reconciliation", "mapping": {key: key for key in keys}}))
        assert recon["status"] == "completed" and recon["recon_source_id"] and not recon["version_id"], recon
        sources = admin.get("/api/v1/reconciliation-sources", params={"organization_id": str(ORG)}).json()
        assert any(item["id"] == recon["recon_source_id"] for item in sources)
        partner_sources = []
        for domain, content in (
            ("inventory", "transaction_id,product_id,branch_id,date,quantity,unit\nstock1,sku1,branch1,2026-10-12,2,ea\n"),
            ("payments", "payment_id,invoice_id,date,amount,invoice_amount\npay1,invoice1,2026-10-13,49,49\n"),
        ):
            uploaded = admin.post("/api/v1/reconciliation-sources", data={
                "organization_id": str(ORG), "source": "fixture-" + domain, "domain": domain,
            }, files={"file": (domain + ".csv", content.encode())})
            assert uploaded.status_code == 201, uploaded.text
            partner_sources.append(uploaded.json()["id"])
        runs = "/api/v1/reconciliation-runs"
        reconciliation = admin.post(runs, json={
            "organization_id": str(ORG), "input_ids": [recon["recon_source_id"], *partner_sources],
            "as_of": "2026-10-31", "request_key": str(uuid4()),
        })
        assert reconciliation.status_code == 202, reconciliation.text
        run_path = runs + "/" + reconciliation.json()["id"]
        deadline = monotonic() + 25
        while monotonic() < deadline:
            evaluated = admin.get(run_path, params={"organization_id": str(ORG)}).json()
            if evaluated["status"] != "queued":
                break
            sleep(.15)
        assert evaluated["status"] == "completed" and recon["recon_source_id"] in evaluated["input_ids"], evaluated
        assert admin.patch(target, json={"enabled": False}).status_code == 200
        assert admin.post(target + "/test").status_code == 409
        assert admin.delete(target).status_code == 204
        assert admin.get(target + "/syncs").status_code == 404
