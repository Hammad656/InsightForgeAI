"""Operator-scoped PostgreSQL destinations and read-only extraction."""
import csv
import hashlib
import io
import ipaddress
import re
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import BoundedSemaphore
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.config import get_settings
from app.ingestion import FIELDS, MAX_BYTES, MAX_CELL, MAX_ROWS
from app.reconciliation import REQUIRED, SourceOptions

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")]
ProfileKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")]
GLOBAL_UNICAST = ipaddress.ip_network("2000::/3")
SLOTS = BoundedSemaphore(2)
TYPES = {"text", "varchar", "bpchar", "int2", "int4", "int8", "numeric", "date", "uuid"}


class ConnectorError(RuntimeError):
    """Fixed public error code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Object(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: Identifier
    table: Identifier

    @model_validator(mode="after")
    def business_schema(self) -> "Object":
        if self.schema_name.lower().startswith("pg_") or self.schema_name.lower() == "information_schema":
            raise ValueError("Select a business schema")
        return self


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(max_length=253, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
    port: int = Field(ge=1, le=65535)
    address: str
    database: Identifier
    username: Identifier
    secret_ref: ProfileKey
    ca_file: Path | None = None
    objects: list[Object] = Field(min_length=1, max_length=32)
    isolated_loopback: bool = False


class ConnectorSourceOptions(SourceOptions):
    model_config = ConfigDict(extra="forbid")


class Selection(Object):
    purpose: Literal["analytics", "reconciliation"] = "analytics"
    domain: Literal["sales", "inventory", "payments", "invoices"]
    mapping: dict[Identifier, Identifier] = Field(min_length=1, max_length=32)
    source_options: ConnectorSourceOptions = Field(default_factory=ConnectorSourceOptions)

    @model_validator(mode="after")
    def mapped(self) -> "Selection":
        fields = (None if self.domain == "invoices" else FIELDS[self.domain]) if self.purpose == "analytics" else REQUIRED[self.domain]
        optional = {"invoice_amount", "invoice_source"} if self.domain == "payments" and self.purpose == "reconciliation" else set()
        if (fields is None or not set(fields).issubset(self.mapping) or
                not set(self.mapping).issubset(set(fields) | optional)
                or len(set(self.mapping.values())) != len(self.mapping)):
            raise ValueError("Map all required fields to distinct approved columns")
        return self


def profiles(org: UUID) -> dict[str, Any]:
    return get_settings().pg_connector_profiles.get(str(org), {})


def profile(org: UUID, key: str) -> Profile:
    item = profiles(org).get(key)
    if not item:
        raise ConnectorError("profile_unavailable")
    try:
        return Profile.model_validate(item)
    except ValidationError:
        raise ConnectorError("profile_unavailable") from None


def pinned_address(endpoint: Profile) -> str:
    settings = get_settings()
    try:
        address = ipaddress.ip_address(endpoint.address)
        resolved = {ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(
            endpoint.host, endpoint.port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
        )}
    except (OSError, ValueError):
        raise ConnectorError("destination_denied") from None
    test_local = (endpoint.isolated_loopback and settings.pg_connector_allow_local_test
                  and not settings.cookie_secure and address.is_loopback)
    public = (address.is_global and not address.is_multicast and not address.is_reserved
              and not address.is_link_local and not address.is_loopback
              and not (isinstance(address, ipaddress.IPv6Address) and (
                  address not in GLOBAL_UNICAST or address.ipv4_mapped or address.sixtofour or address.teredo
              )))
    if resolved != {address} or not (test_local or public and not endpoint.isolated_loopback):
        raise ConnectorError("destination_denied")
    if not test_local and (not endpoint.ca_file or not endpoint.ca_file.is_file()):
        raise ConnectorError("tls_configuration_required")
    return str(address)


def password(endpoint: Profile) -> str:
    root = get_settings().pg_connector_secret_dir
    if root is None:
        raise ConnectorError("secret_unavailable")
    try:
        path = (root / endpoint.secret_ref).resolve(strict=True)
        if path.parent != root.resolve(strict=True):
            raise ValueError
        with path.open("r", encoding="utf-8") as stream:
            raw = stream.read(4099)
        value = raw.rstrip("\r\n")
        if not value or len(raw) >= 4099 or len(value) > 4096 or "\x00" in value:
            raise ValueError
    except (OSError, ValueError):
        raise ConnectorError("secret_unavailable") from None
    return value


def check_role(conn: psycopg.Connection[Any], endpoint: Profile) -> None:
    allowed = {(item.schema_name, item.table) for item in endpoint.objects}
    safe = conn.execute("""
      SELECT current_user=session_user AND NOT (r.rolsuper OR r.rolbypassrls OR r.rolcreaterole
        OR r.rolcreatedb OR r.rolreplication)
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid)
      AND NOT has_database_privilege(current_database(),'CREATE,TEMP')
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_database d WHERE d.datdba=r.oid)
      FROM pg_catalog.pg_roles r WHERE rolname=current_user
    """).fetchone()
    if not safe or not safe[0]:
        raise ConnectorError("unsafe_source_role")
    unsafe = conn.execute("""
      SELECT EXISTS (
        SELECT 1 FROM pg_catalog.pg_namespace n
        WHERE left(n.nspname,3)<>'pg_' AND n.nspname <> 'information_schema'
          AND (has_schema_privilege(n.oid,'CREATE') OR n.nspowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user))
      ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p WHERE p.proowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)
      ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_type t WHERE t.typowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)
      ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c WHERE c.relowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)
      ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c
        WHERE CASE WHEN c.relkind='S' THEN has_sequence_privilege(c.oid,'SELECT,USAGE,UPDATE') ELSE false END
      )
    """).fetchone()
    if unsafe and unsafe[0]:
        raise ConnectorError("unsafe_source_role")
    found = set()
    for schema, name, kind, persistent, owned, readable, writable in conn.execute("""
      SELECT n.nspname,c.relname,c.relkind,c.relpersistence,
             c.relowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user),
             has_any_column_privilege(c.oid,'SELECT'),
             has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
               OR has_any_column_privilege(c.oid,'INSERT,UPDATE,REFERENCES')
      FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE c.relkind IN ('r','p','v','m','f') AND left(n.nspname,3)<>'pg_'
        AND n.nspname <> 'information_schema' LIMIT 5001
    """):
        if owned or writable or readable and ((schema, name) not in allowed or kind != "r" or persistent != "p"):
            raise ConnectorError("unsafe_source_role")
        if readable:
            found.add((schema, name))
    count = conn.execute("""
      SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE c.relkind IN ('r','p','v','m','f') AND left(n.nspname,3)<>'pg_'
        AND n.nspname <> 'information_schema'
    """).fetchone()
    if not count or count[0] > 5000 or found != allowed:
        raise ConnectorError("source_objects_unavailable")


@contextmanager
def remote(endpoint: Profile) -> Iterator[psycopg.Connection[Any]]:
    if not SLOTS.acquire(blocking=False):
        raise ConnectorError("connector_busy")
    try:
        address = pinned_address(endpoint)
        kwargs: dict[str, Any] = {"host": endpoint.host, "hostaddr": address, "port": endpoint.port,
                                  "dbname": endpoint.database, "user": endpoint.username,
                                  "password": password(endpoint), "connect_timeout": 4,
                                  "sslmode": "disable" if endpoint.isolated_loopback and not endpoint.ca_file else "verify-full",
                                  "gssencmode": "disable", "application_name": "insightforge_connector",
                                  "options": "-c search_path=pg_catalog -c statement_timeout=4000 -c lock_timeout=750"}
        if endpoint.ca_file:
            kwargs["sslrootcert"] = str(endpoint.ca_file)
        with psycopg.connect(**kwargs) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            check_role(conn, endpoint)
            yield conn
    except psycopg.OperationalError as exc:
        code = "query_timeout" if exc.sqlstate in {"57014", "55P03"} else "source_connection_failed"
        raise ConnectorError(code) from None
    except psycopg.Error:
        raise ConnectorError("source_query_denied") from None
    finally:
        SLOTS.release()


def columns(conn: psycopg.Connection[Any], endpoint: Profile, target: Object) -> list[dict[str, str]]:
    if not any((item.schema_name, item.table) == (target.schema_name, target.table) for item in endpoint.objects):
        raise ConnectorError("source_object_denied")
    results = conn.execute("""
      SELECT a.attname,t.typname FROM pg_catalog.pg_attribute a
      JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
      JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
      WHERE n.nspname=%s AND c.relname=%s AND c.relkind='r' AND tn.nspname='pg_catalog'
        AND a.attnum>0 AND NOT a.attisdropped AND has_column_privilege(c.oid,a.attnum,'SELECT')
      ORDER BY a.attnum LIMIT 257
    """, (target.schema_name, target.table)).fetchall()
    if len(results) > 256:
        raise ConnectorError("source_objects_unavailable")
    return [{"name": name, "type": kind} for name, kind in results
            if kind in TYPES and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name)]


def extract(endpoint: Profile, selection: Selection, preview: bool = False) -> tuple[list[str], list[list[str]], bytes]:
    with remote(endpoint) as conn:
        supported = {column["name"] for column in columns(conn, endpoint, selection)}
        if not set(selection.mapping.values()).issubset(supported):
            raise ConnectorError("source_column_denied")
        headers = sorted(selection.mapping)
        fields = [selection.mapping[name] for name in headers]
        projected = sql.SQL(",").join(sql.SQL("left({}::text, {})").format(
            sql.Identifier(field), sql.Literal(MAX_CELL + 1),
        ) for field in fields)
        statement = sql.SQL("SELECT {} FROM ONLY {} ORDER BY {} LIMIT %s").format(
            projected, sql.Identifier(selection.schema_name, selection.table),
            sql.SQL(",").join(sql.SQL(str(index)) for index in range(1, len(fields) + 1)),
        )
        output = io.StringIO(newline="")
        csv.writer(output).writerow(headers)
        line = io.StringIO(newline="")
        writer = csv.writer(line)
        size = len(output.getvalue().encode())
        rows: list[list[str]] = []
        with conn.cursor(name="insightforge_source") as cursor:
            cursor.execute(statement, (10 if preview else MAX_ROWS + 1,))
            while batch := cursor.fetchmany(100):
                for item in batch:
                    row = [value if value is not None else "" for value in item]
                    if len(rows) >= MAX_ROWS or any(len(value) > MAX_CELL for value in row):
                        raise ConnectorError("source_limit_exceeded")
                    line.seek(0)
                    line.truncate()
                    writer.writerow(row)
                    serialized = line.getvalue()
                    size += len(serialized.encode())
                    if size > MAX_BYTES:
                        raise ConnectorError("source_limit_exceeded")
                    output.write(serialized)
                    rows.append(row)
        if not rows and not preview:
            raise ConnectorError("empty_source")
        return headers, rows, output.getvalue().encode()


def content_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
