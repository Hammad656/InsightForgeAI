"""Exercise the API and worker against PostgreSQL as the restricted runtime role."""
import io
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from redis import Redis
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.api.v1.auth import digest, password_hash
from app.api.v1.datasets import preview_data
from app.core.config import get_settings
from app.core.database import get_engine
from app.core.tenant import set_org
from app.ingestion import FIELDS, InvalidFile, parse, validate
from app.jobs.imports import import_snapshot
from app.main import app

HEADERS = {"Origin": "http://localhost:3000", "X-CSRF": "1"}
SAMPLES = Path(__file__).resolve().parents[2] / "frontend/public/samples"
PASSWORD = "synthetic-test-password"


@pytest.mark.parametrize("content", [
    b"sku\n=1+1\n",
    b"sku\n+1+1\n",
    b"sku\n-1+1\n",
    b"sku\n@SUM(A1)\n",
    b"=1+1\nsample\n",
])
def test_csv_rejects_formulas(content: bytes) -> None:
    with pytest.raises(InvalidFile, match="Spreadsheet formulas"):
        parse("formula.csv", content)


def test_csv_preserves_signed_numbers() -> None:
    assert parse("inventory.csv", b"quantity\n-1\n+2.25\n") == (["quantity"], [["-1"], ["+2.25"]])


@pytest.mark.parametrize(("filename", "content"), [
    ("data.txt", b"sku\nexample\n"),
    ("data.csv", b'sku\n"unterminated\n'),
    ("data.csv", b"sku,sku\na,b\n"),
    ("data.csv", b"sku,branch\na\n"),
])
def test_invalid_tabular_layout(filename: str, content: bytes) -> None:
    with pytest.raises(InvalidFile):
        parse(filename, content)


@pytest.mark.parametrize(("field", "value"), [("sku", ""), ("quantity", "not a number")])
def test_invalid_inventory_fields(field: str, value: str) -> None:
    headers, rows = parse("inventory.csv", (SAMPLES / "inventory.csv").read_bytes())
    rows[0][headers.index(field)] = value
    _, errors = validate("inventory", {name: name for name in headers}, headers, rows)
    assert errors == [{"row": 2, "message": f"Invalid {field}"}]


def test_csv_fixtures_and_xlsx() -> None:
    for domain, fields in FIELDS.items():
        filename = f"{domain}.csv"
        headers, rows = parse(filename, (SAMPLES / filename).read_bytes())
        normalized, errors = validate(domain, dict(zip(fields, fields, strict=True)), headers, rows)
        assert len(normalized) == 2
        assert errors == []
    book = Workbook()
    sheet = book.active
    sheet.append(["id"])
    sheet.append(["sample"])
    content = io.BytesIO()
    book.save(content)
    assert parse("valid.xlsx", content.getvalue()) == (["id"], [["sample"]])
    sheet.append(["=2+2"])
    content = io.BytesIO()
    book.save(content)
    with pytest.raises(InvalidFile):
        parse("formula.xlsx", content.getvalue())
    with pytest.raises(InvalidFile):
        parse("bad.xlsx", b"not a workbook")


def test_mapping_suggestions_require_unique_matches() -> None:
    headers = "line_id,OrderNumber,CustomerCode,ProductCode,BranchCode,TransactionDate,quantity,SellingPrice"
    tail = ",discount,unit_cost,status,currency"
    values = "line,order,customer,product,branch,2025-01-01,1,10,0,3,completed,PKR"
    result = preview_data("aliases.csv", f"{headers}{tail}\n{values}\n".encode())
    assert result["suggested_domain"] == "sales"
    assert result["suggestions"]["sales"]["mapping"]["order_id"] == "OrderNumber"
    assert result["suggestions"]["sales"]["mapping"]["unit_price"] == "SellingPrice"
    result = preview_data("ambiguous.csv", f"{headers}{tail},order_id\n{values},other\n".encode())
    assert result["suggested_domain"] is None
    assert result["suggestions"]["sales"]["ambiguous"] == ["order_id"]
    assert "order_id" not in result["suggestions"]["sales"]["mapping"]


@pytest.fixture
def integration():
    if os.getenv("M2_INTEGRATION") != "1":
        pytest.skip("M2_INTEGRATION=1 requires migrated PostgreSQL and Redis")
    with TestClient(app, base_url="http://localhost", headers=HEADERS) as first:
        with TestClient(app, base_url="http://localhost", headers=HEADERS) as second:
            yield first, second


def register(client):
    body = {"email": f"{uuid4()}@example.test", "password": PASSWORD}
    response = client.post("/api/v1/auth/register", json=body)
    assert response.status_code == 201, response.text
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert all("HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/api/v1" in cookie for cookie in cookies)
    return body


def org(client):
    response = client.post("/api/v1/organizations", json={"name": "Synthetic organization"})
    assert response.status_code == 201, response.text
    return f"/api/v1/organizations/{response.json()['id']}"


def test_session_lifecycle(integration) -> None:
    client, replay = integration
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get(f"/api/v1/organizations/{uuid4()}/datasets").status_code == 401
    credentials = register(client)
    refresh = client.cookies.get("if_refresh")
    access = client.cookies.get("if_access")
    response = client.post("/api/v1/auth/refresh", headers={"Origin": "https://untrusted.test"})
    assert response.status_code == 403
    assert client.post("/api/v1/auth/refresh", headers={"X-CSRF": ""}).status_code == 403
    with get_engine().begin() as db:
        stored_hash = db.execute(text("SELECT password_hash FROM app.users WHERE email=:email"),
                                 credentials).scalar_one()
        salt, hashed_password = stored_hash.split(":")
        assert len(bytes.fromhex(salt)) == 16
        assert hashed_password == password_hash(PASSWORD, salt)
        db.execute(text("""
            UPDATE app.sessions SET access_until=now()-interval '1 second'
            WHERE user_id=(SELECT id FROM app.users WHERE email=:email)
        """), credentials)
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/refresh").status_code == 204
    assert client.cookies.get("if_refresh") != refresh
    replay.cookies.set("if_refresh", refresh)
    replay.cookies.set("if_access", access)
    assert replay.post("/api/v1/auth/refresh").status_code == 401
    assert replay.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 200
    replay.cookies.set("if_access", client.cookies.get("if_access"))
    replay.cookies.set("if_refresh", client.cookies.get("if_refresh"))
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert replay.get("/api/v1/auth/me").status_code == 401
    assert replay.post("/api/v1/auth/refresh").status_code == 401
    assert client.post("/api/v1/auth/login", json={**credentials, "password": "incorrect-password"}).status_code == 401
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 200
    with get_engine().begin() as db:
        db.execute(text("""
            UPDATE app.sessions SET expires_at=now()-interval '1 second'
            WHERE refresh_hash=:refresh
        """), {"refresh": digest(client.cookies.get("if_refresh"))})
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/refresh").status_code == 401
    # Prime the account counter without running repeated password hashes.
    with Redis.from_url(get_settings().redis_url) as redis:
        redis.set(f"if:auth:account:{digest(credentials['email'])}", 12, ex=60)
    throttled = client.post("/api/v1/auth/login", json=credentials)
    assert throttled.status_code == 429
    assert throttled.headers["Retry-After"] == "60"


def test_tenant_api_worker_and_atomic_publication(integration, monkeypatch) -> None:
    first, second = integration
    register(first)
    second_credentials = register(second)
    a, b = org(first), org(second)
    aid, bid = a.rsplit("/", 1)[1], b.rsplit("/", 1)[1]
    # Process the same task function deterministically when an external worker is absent.
    if os.getenv("M2_LIVE_WORKER") != "1":
        monkeypatch.setattr(import_snapshot, "apply_async", lambda args, **kwargs: import_snapshot.run(*args))
    sales = (SAMPLES / "sales.csv").read_bytes()

    def upload(client, organization, content):
        return client.post(organization + "/datasets/uploads", files={"file": ("sales.csv", content)})

    assert upload(first, a, b"header only").status_code == 422
    assert upload(first, a, b" " * (6 * 1024**2)).status_code == 413
    staged = upload(first, a, sales)
    assert staged.status_code == 201, staged.text
    upload_id = staged.json()["id"]
    preview = f"/datasets/uploads/{upload_id}/preview"
    assert first.get(a + preview).status_code == 200
    assert second.get(a + preview).status_code == 404
    assert second.get(b + preview).status_code == 404
    assert second.get(a + "/members").status_code == 404
    assert second.get(a + "/datasets/imports").status_code == 404
    assert second.get(a + "/datasets/uploads").status_code == 404
    assert second.delete(b + f"/datasets/uploads/{upload_id}").status_code == 404
    mapping = {field: field for field in FIELDS["sales"]}
    payload = {"upload_id": upload_id, "domain": "sales", "mapping": mapping}
    assert second.post(b + "/datasets/imports", json=payload).status_code == 404
    assert first.post(a + "/datasets/imports", json={**payload, "mapping": {}}).status_code == 422

    def wait_for(job_id):
        deadline = monotonic() + 20
        while monotonic() < deadline:
            response = first.get(a + f"/jobs/{job_id}")
            assert response.status_code == 200
            if response.json()["status"] != "queued":
                return response.json()
            sleep(0.15)
        pytest.fail("Worker did not finish import")

    submitted = first.post(a + "/datasets/imports", json=payload)
    assert submitted.status_code == 202, submitted.text
    job = submitted.json()["id"]
    assert second.get(b + f"/jobs/{job}").status_code == 404
    assert second.get(a + f"/jobs/{job}").status_code == 404
    import_snapshot.run(bid, job)
    assert wait_for(job)["row_count"] == 2
    import_snapshot.run(aid, job)
    duplicate = first.post(a + "/datasets/imports", json=payload).json()
    assert duplicate["id"] == job and duplicate["duplicate"] is True
    bad = upload(first, a, sales + sales.splitlines()[1] + b"\n").json()["id"]
    failed = first.post(a + "/datasets/imports", json={**payload, "upload_id": bad}).json()["id"]
    assert wait_for(failed)["errors"] == [{"row": 4, "message": "Duplicate source identifier"}]
    assert first.get(a + "/datasets").json() == [{"domain": "sales", "active_version": job}]
    assert second.get(b + "/datasets").json() == []
    with get_engine().begin() as db:
        assert db.execute(text("SELECT current_user")).scalar_one() == "insightforge_app"
        assert tuple(db.execute(text("""
            SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole
            FROM pg_roles WHERE rolname=current_user
        """)).one()) == (False, False, False, False)
        assert db.execute(text("""
            SELECT count(*) FROM pg_class
            WHERE oid IN ('app.uploads'::regclass, 'app.import_jobs'::regclass,
                          'app.datasets'::regclass, 'biz.dataset_versions'::regclass,
                          'biz.dataset_rows'::regclass)
            AND relrowsecurity AND relforcerowsecurity
            AND relowner <> (SELECT oid FROM pg_roles WHERE rolname=current_user)
        """)).scalar_one() == 5
        assert db.execute(text("SELECT count(*) FROM app.uploads")).scalar_one() == 0
        set_org(db, UUID(bid))
        for table in ("app.uploads", "app.import_jobs", "biz.dataset_versions", "biz.dataset_rows"):
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        result = db.execute(text("UPDATE app.import_jobs SET status='failed' WHERE id=:id"), {"id": UUID(job)})
        assert result.rowcount == 0
        with pytest.raises(ProgrammingError, match="row-level security"), db.begin_nested():
            db.execute(text("INSERT INTO app.datasets (org_id,domain) VALUES (:org,'inventory')"), {"org": UUID(aid)})
        set_org(db, UUID(aid))
        assert db.execute(text("SELECT count(*) FROM biz.dataset_rows")).scalar_one() == 2

    for domain in ("inventory", "payments"):
        sample = (SAMPLES / f"{domain}.csv").read_bytes()
        filename = f"{domain}.csv"
        if domain == "inventory":
            book = Workbook()
            for line in sample.decode().splitlines():
                book.active.append(line.split(","))
            buffer = io.BytesIO()
            book.save(buffer)
            sample, filename = buffer.getvalue(), "inventory.xlsx"
        response = first.post(a + "/datasets/uploads", files={"file": (filename, sample)})
        assert response.status_code == 201, response.text
        imported = first.post(a + "/datasets/imports", json={
            "upload_id": response.json()["id"], "domain": domain,
            "mapping": {field: field for field in FIELDS[domain]},
        })
        assert imported.status_code == 202, imported.text
        assert wait_for(imported.json()["id"])["row_count"] == 2

    invitation = first.post(a + "/invitations", json={"email": second_credentials["email"], "role": "viewer"})
    assert invitation.status_code == 201, invitation.text
    token = invitation.json()
    assert first.post(a + "/invitations/accept", json=token).status_code == 404
    assert second.post(a + "/invitations/accept", json=token).status_code == 204
    assert second.post(a + "/invitations/accept", json=token).status_code == 404
    assert second.get(a + "/datasets").status_code == 200
    assert second.get(a + preview).status_code == 403
    assert second.get(a + "/datasets/uploads").status_code == 403
    assert second.delete(a + f"/datasets/uploads/{upload_id}").status_code == 403
    assert upload(second, a, sales).status_code == 403
    assert second.get(a + "/members").status_code == 403
    first_id = first.get("/api/v1/auth/me").json()["id"]
    assert first.patch(a + f"/members/{first_id}", json={"role": "viewer"}).status_code == 409
    uid = second.get("/api/v1/auth/me").json()["id"]
    assert first.patch(a + f"/members/{uid}", json={"role": "analyst"}).status_code == 204
    assert second.get(a + "/datasets").status_code == 200
    assert second.post(a + "/datasets/imports", json=payload).status_code == 403
    assert second.post(a + f"/datasets/versions/{job}/archive").status_code == 403
    assert first.delete(a + f"/members/{uid}").status_code == 204
    assert second.get(a + "/datasets").status_code == 404
    assert second.get(b + "/datasets").status_code == 200


def test_dataset_lifecycle_and_late_worker_delivery(integration, monkeypatch) -> None:
    client, other = integration
    register(client)
    register(other)
    path = org(client)
    other_path = org(other)
    tenant_id = path.rsplit("/", 1)[1]
    staging = path + "/datasets/uploads"
    versions = path + "/datasets/versions"
    sales = (SAMPLES / "sales.csv").read_bytes()
    monkeypatch.setattr(import_snapshot, "apply_async", lambda **kwargs: None)

    def upload(content: bytes, endpoint: str = staging) -> str:
        result = client.post(endpoint, files={"file": ("synthetic.csv", content)})
        assert result.status_code == 201, result.text
        return str(result.json()["id"])

    def submit(source: str) -> str:
        response = client.post(path + "/datasets/imports", json={
            "upload_id": source, "domain": "sales", "mapping": {field: field for field in FIELDS["sales"]},
        })
        assert response.status_code == 202, response.text
        return str(response.json()["id"])

    wrong = upload(b"unexpected\n123\n")
    assert client.get(staging + f"/{wrong}/preview").json()["suggested_domain"] is None
    listed = client.get(staging).json()[0]
    assert (listed["id"], listed["row_count"], listed["size_bytes"], listed["deletable"]) == (wrong, 1, 15, True)
    assert client.delete(staging + f"/{wrong}", headers={"X-CSRF": ""}).status_code == 403
    assert client.delete(staging + f"/{wrong}").status_code == 204
    assert client.get(staging).json() == []
    assert client.get(staging + f"/{wrong}/preview").status_code == 404

    unused = upload(sales)
    source = upload(sales, staging + f"?replace_upload_id={unused}")
    assert client.get(staging + f"/{unused}/preview").status_code == 404
    cancelled = submit(source)
    cancel = path + f"/jobs/{cancelled}/cancel"
    assert client.post(cancel).json()["status"] == "cancelled"
    assert client.post(cancel).json()["status"] == "cancelled"
    import_snapshot.run(tenant_id, cancelled)
    assert client.get(versions).json() == []
    assert client.delete(staging + f"/{source}").status_code == 409

    published = submit(source)
    assert published != cancelled
    import_snapshot.run(tenant_id, published)
    assert client.get(versions).json()[0]["status"] == "active"
    assert client.post(path + f"/jobs/{published}/cancel").status_code == 409
    queued = submit(upload(sales + b"\n"))
    archive = versions + f"/{published}/archive"
    assert other.post(other_path + f"/datasets/versions/{published}/archive").status_code == 404
    assert client.post(archive).status_code == 204
    assert client.post(archive).status_code == 204
    import_snapshot.run(tenant_id, queued)
    assert client.get(path + "/datasets").json() == []
    assert client.get(path + "/analytics/sales").json()["reports"]["sales"]["source"] is None
    snapshots = {item["id"]: item["status"] for item in client.get(versions).json()}
    assert snapshots == {published: "archived", queued: "previous"}
    restore = versions + f"/{published}/restore"
    assert client.post(restore).status_code == 204
    assert client.post(restore).status_code == 204
    assert client.get(path + "/datasets").json()[0]["active_version"] == published
    assert client.get(path + "/analytics/sales").json()["reports"]["sales"]["source"]["version"] == published
    with get_engine().begin() as db:
        set_org(db, UUID(tenant_id))
        assert db.execute(text("SELECT count(*) FROM biz.dataset_rows WHERE version_id=:id"),
                          {"id": UUID(published)}).scalar_one() == 2
        db.execute(text("""
            INSERT INTO app.uploads (org_id,id,uploader,filename,digest,content)
            SELECT u.org_id,gen_random_uuid(),u.uploader,u.filename,u.digest,u.content
            FROM app.uploads u
            CROSS JOIN generate_series(1, 100 - (SELECT count(*) FROM app.uploads WHERE org_id=:org))
            WHERE u.org_id=:org AND u.id=:id
        """), {"org": UUID(tenant_id), "id": UUID(source)})
    assert client.post(staging, files={"file": ("overflow.csv", sales)}).status_code == 413
    unused_at_limit = next(item["id"] for item in client.get(staging).json() if item["deletable"])
    assert client.delete(staging + f"/{unused_at_limit}").status_code == 204
    assert upload(sales)
