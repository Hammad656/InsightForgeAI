"""Atomic publication of approved source snapshots through the ingestion pipeline."""
import json
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.audit import audit
from app.ingestion import FIELDS, Domain, InvalidFile, parse, validate
from app.jobs.imports import publish_validated
from app.pg_connector import ConnectorError, Selection, content_digest


def stage(
    db: Connection, org: UUID, connector: UUID, actor: UUID, selection: Selection, content: bytes, created_at: datetime,
) -> tuple[int, UUID | None, UUID | None]:
    try:
        headers, rows = parse("connector.csv", content)
    except InvalidFile:
        raise ConnectorError("invalid_source_data") from None
    identity, digest = uuid4(), content_digest(content)
    data: dict[str, Any] = {"org": org, "id": identity, "actor": actor, "domain": selection.domain,
            "source": "pg-" + connector.hex, "digest": digest, "content": content,
            "options": selection.source_options.model_dump_json()}
    if selection.purpose == "reconciliation":
        prior = db.execute(text("""
          SELECT id FROM app.recon_sources WHERE org_id=:org AND source=:source AND domain=:domain
          AND digest=:digest AND options=CAST(:options AS jsonb) ORDER BY revision DESC LIMIT 1
        """), data).scalar_one_or_none()
        if prior:
            return len(rows), None, prior
        quota(db, org, len(content), True)
        source = db.execute(text("""
          INSERT INTO app.recon_sources(org_id,id,source,domain,revision,filename,digest,original,
                                        header,records,options,actor_id)
          SELECT :org,:id,:source,:domain,COALESCE(max(revision),0)+1,'connector.csv',:digest,:content,
                 CAST(:headers AS jsonb),CAST(:rows AS jsonb),CAST(:options AS jsonb),:actor
          FROM app.recon_sources WHERE org_id=:org AND source=:source AND domain=:domain RETURNING id
        """), data | {"headers": json.dumps(headers), "rows": json.dumps(rows)}).scalar_one()
        audit(db, "reconciliation.source_uploaded", org=org, actor=actor, resource=source)
        return len(rows), None, source
    domain = cast(Domain, selection.domain)
    mapping = {key: key for key in FIELDS[domain]}
    validated, errors = validate(domain, mapping, headers, rows)
    if errors:
        raise ConnectorError("invalid_source_data")
    # Include the source configuration in the content fingerprint for provenance.
    data["fingerprint"] = content_digest(
        (digest + str(connector) + selection.model_dump_json()).encode(),
    )
    prior = db.execute(text("""
      SELECT id FROM app.import_jobs WHERE org_id=:org AND domain=:domain AND fingerprint=:fingerprint
        AND status='published'
    """), data).scalar_one_or_none()
    if prior:
        return len(validated), prior, None
    quota(db, org, len(content), False)
    db.execute(text("""
      INSERT INTO app.uploads(org_id,id,uploader,filename,digest,content)
      VALUES (:org,:id,:actor,'connector.csv',:digest,:content)
    """), data)
    audit(db, "dataset.uploaded", org=org, actor=actor, resource=identity)
    db.execute(text("INSERT INTO app.datasets(org_id,domain) VALUES (:org,:domain) ON CONFLICT DO NOTHING"), data)
    db.execute(text("""
      INSERT INTO app.import_jobs(org_id,id,domain,upload_id,requested_by,mapping,fingerprint,created_at)
      VALUES (:org,:id,:domain,:id,:actor,CAST(:mapping AS jsonb),:fingerprint,:created)
    """), data | {"mapping": json.dumps(mapping), "created": created_at})
    publish_validated(db, data | {"created": created_at}, validated, actor)
    return len(validated), identity, None


def quota(db: Connection, org: UUID, size: int, reconciliation: bool) -> None:
    query = ("SELECT count(*),COALESCE(sum(octet_length(original)),0) FROM app.recon_sources WHERE org_id=:org"
             if reconciliation else
             "SELECT count(*),COALESCE(sum(octet_length(content)),0) FROM app.uploads WHERE org_id=:org")
    count, used = db.execute(text(query), {"org": org}).one()
    if count >= (64 if reconciliation else 100) or used + size > 100 * 1024**2:
        raise ConnectorError("staging_quota_exceeded")


def mark_error(db: Connection, args: dict[str, Any], code: str, retry: bool) -> str | None:
    return db.execute(text("""
      UPDATE app.pg_syncs SET status=:status,error_code=:code,lease_token=NULL,lease_until=NULL,
           finished_at=CASE WHEN :retry THEN NULL ELSE clock_timestamp() END
      WHERE org_id=:org AND id=:id AND lease_token=:lease RETURNING status
    """), args | {"code": code, "retry": retry, "status": "queued" if retry else "failed"}).scalar_one_or_none()
