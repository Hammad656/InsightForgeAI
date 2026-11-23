"""Provision the disposable external source and organization-scoped profiles."""
import json
import os
from pathlib import Path
from uuid import UUID

import psycopg

ORG = UUID("aa2eb1a4-144f-47ed-92ff-fa7017540c06")
OBJECTS = [{"schema_name": "feed", "table": "sales"}, {"schema_name": "feed", "table": "reconciliation"}]
OWNER = "source_owner_fixture"
READER = "synthetic_reader_fixture"
PORT = int(os.getenv("M6E_SOURCE_PORT", "15439"))


def owner() -> psycopg.Connection:
    return psycopg.connect(host="127.0.0.1", port=PORT, dbname="external_fixture",
                           user="source_owner", password=OWNER, connect_timeout=4)


def provision() -> None:
    with owner() as db:
        db.execute("""
          DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='connector_reader') THEN
              CREATE ROLE connector_reader LOGIN PASSWORD 'synthetic_reader_fixture';
            END IF;
          END $$;
          REVOKE TEMP, CREATE ON DATABASE external_fixture FROM PUBLIC;
          REVOKE CREATE ON SCHEMA public FROM PUBLIC;
          CREATE SCHEMA IF NOT EXISTS feed;
          CREATE TABLE IF NOT EXISTS feed.sales(
            line_id text PRIMARY KEY, order_id text, customer_id text, sku text, branch_id text,
            ordered_on date, quantity numeric, unit_price numeric, discount numeric, unit_cost numeric,
            status text, currency text, restricted_value text
          );
          CREATE TABLE IF NOT EXISTS feed.reconciliation(
            transaction_id text, customer_id text, product_id text, branch_id text, invoice_id text,
            date date, amount numeric
          );
          CREATE TABLE IF NOT EXISTS feed.private_feed(private_value text);
          TRUNCATE feed.sales,feed.reconciliation;
          INSERT INTO feed.sales VALUES
            ('line1','order1','customer1','sku1','branch1','2026-10-12',2,25,1,10,'completed','PKR','restricted');
          INSERT INTO feed.reconciliation VALUES
            ('line1','customer1','sku1','branch1','invoice1','2026-10-12',49);
          GRANT USAGE ON SCHEMA feed TO connector_reader;
          GRANT SELECT(line_id,order_id,customer_id,sku,branch_id,ordered_on,quantity,unit_price,discount,
                       unit_cost,status,currency) ON feed.sales TO connector_reader;
          GRANT SELECT ON feed.reconciliation TO connector_reader;
        """)


def environment(directory: Path) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reader").write_text(READER, encoding="utf-8")
    (directory / "wrong").write_text("invalid_fixture_password", encoding="utf-8")
    profile = {"host": "127.0.0.1", "address": "127.0.0.1", "port": PORT, "database": "external_fixture",
               "username": "connector_reader", "objects": OBJECTS, "secret_ref": "reader", "isolated_loopback": True}
    profiles = {"approved": profile, "bad-auth": profile | {"secret_ref": "wrong"}}
    if ca_file := os.getenv("M6E_SOURCE_CA"):
        profiles["bad-tls"] = profile | {"ca_file": ca_file}
    if ca_file := os.getenv("M6E_TRUSTED_SOURCE_CA"):
        profiles["trusted-tls"] = profile | {"ca_file": ca_file}
    return {"PG_CONNECTOR_SECRET_DIR": str(directory), "PG_CONNECTOR_ALLOW_LOCAL_TEST": "true",
            "PG_CONNECTOR_PROFILES": json.dumps({str(ORG): profiles})}


if __name__ == "__main__":
    provision()
    directory = Path(os.environ["M6E_SECRET_FIXTURE_DIR"])
    config = environment(directory)
    if path := os.getenv("GITHUB_ENV"):
        with Path(path).open("a", encoding="utf-8") as stream:
            for key, value in config.items():
                stream.write(f"{key}={value}\n")
    else:
        print(json.dumps(config))
