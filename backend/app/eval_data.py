"""Load fixed, synthetic snapshots into two membership-free workspaces."""
import json
from typing import Any

from sqlalchemy import text

from app.core.database import get_engine
from app.core.tenant import set_org
from app.eval_fixtures import DAY, LEDGERS, ORG, OTHER, SCHEMAS, SUITE, VERSIONS, fixture_id

OTHER_SALES = fixture_id("control-sales")


def seed() -> dict[str, dict[str, Any]]:
    with get_engine().begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": SUITE})
        actor = fixture_id("fixture-creator")
        db.execute(text("""
            INSERT INTO app.users(id,email,password_hash) VALUES (:id,:email,'!')
            ON CONFLICT(id) DO NOTHING
        """), {"id": actor, "email": f"{actor}@fixture.invalid"})
        for org, versions in ((ORG, VERSIONS), (OTHER, {"sales": OTHER_SALES})):
            set_org(db, org)
            db.execute(text("INSERT INTO app.organizations(id,name) VALUES (:org,:name) ON CONFLICT(id) DO NOTHING"),
                       {"org": org, "name": SUITE})
            for domain, version in versions.items():
                args = {"org": org, "id": version, "actor": actor, "domain": domain, "published": DAY}
                db.execute(text("""
                    INSERT INTO app.uploads(org_id,id,uploader,filename,digest,content)
                    VALUES (:org,:id,:actor,'evaluation','evaluation',decode('','hex')) ON CONFLICT DO NOTHING
                """), args)
                db.execute(text("""
                    INSERT INTO app.datasets(org_id,domain) VALUES (:org,:domain) ON CONFLICT DO NOTHING
                """), args)
                db.execute(text("""
                    INSERT INTO app.import_jobs(org_id,id,upload_id,domain,requested_by,mapping,fingerprint,status)
                    VALUES (:org,:id,:id,:domain,:actor,'{}','evaluation','published') ON CONFLICT DO NOTHING
                """), args)
                db.execute(text("""
                    INSERT INTO biz.dataset_versions(org_id,id,domain,published_at)
                    VALUES (:org,:id,:domain,CAST(:published AS date) + interval '12 hours') ON CONFLICT DO NOTHING
                """), args)
                db.execute(text("""
                    INSERT INTO biz.dataset_rows(org_id,version_id,source_key,payload)
                    VALUES (:org,:id,:index,CAST(:payload AS jsonb)) ON CONFLICT DO NOTHING
                """), [args | {"index": str(index), "payload": json.dumps(
                    dict(zip(SCHEMAS[domain], row, strict=True)) | {"currency": "PKR"},
                )} for index, row in enumerate(LEDGERS[domain], 1)])
                db.execute(text("""
                    UPDATE app.datasets SET active_version=:id
                    WHERE org_id=:org AND domain=:domain AND active_version IS NULL AND lifecycle_changed_at IS NULL
                """), args)
        set_org(db, ORG)
        sources = db.execute(text("""
            SELECT v.domain,v.id,v.published_at FROM biz.dataset_versions v
            JOIN app.datasets d ON (d.org_id,d.domain,d.active_version)=(v.org_id,v.domain,v.id)
            WHERE v.org_id=:org
        """), {"org": ORG}).mappings()
        return {row["domain"]: {"version": str(row["id"]), "published_at": row["published_at"]} for row in sources}
