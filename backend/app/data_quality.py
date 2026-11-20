"""Quality context for analytics evidence and the executive publication gate."""
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text


def quality(db: Connection, org: UUID) -> dict[str, Any] | None:
    latest = db.execute(text("""
        SELECT id,status,as_of,finished_at,summary->>'invoice_mode' AS invoice_mode,
               EXISTS (
                   SELECT 1 FROM app.recon_sources old JOIN app.recon_sources newer
                     ON (newer.org_id,newer.source,newer.domain)=(old.org_id,old.source,old.domain)
                   WHERE old.org_id=:org AND old.id=ANY(r.input_ids) AND newer.revision>old.revision
               ) AS newer_sources
        FROM app.recon_runs r WHERE org_id=:org
        ORDER BY created_at DESC,id DESC LIMIT 1
    """), {"org": org}).mappings().one_or_none()
    if latest is None:
        return None
    counts = db.execute(text("""
        SELECT count(*) FILTER (WHERE severity='material' AND status IN ('unresolved','reviewed')) AS blocking,
               count(*) FILTER (WHERE severity='material' AND status='acknowledged') AS acknowledged,
               count(*) FILTER (WHERE severity='warning' AND status IN ('unresolved','reviewed')) AS warnings
        FROM app.recon_issues WHERE org_id=:org
    """), {"org": org}).mappings().one()
    if counts["blocking"]:
        status = "blocked"
        message = "Material reconciliation exceptions remain open. Executive report publication is blocked."
    elif latest["status"] != "completed":
        status = "pending"
        message = "The latest reconciliation has not completed. Source quality has not been confirmed."
    elif counts["acknowledged"]:
        status = "acknowledged"
        message = "An administrator acknowledged material exceptions. Review these limitations before relying on the figures."
    elif latest["newer_sources"]:
        status = "warnings"
        message = "Newer source versions are available. The recorded reconciliation covers earlier versions."
    elif counts["warnings"]:
        status = "warnings"
        message = "Review open reconciliation warnings before relying on the figures."
    else:
        status = "clear"
        message = "No open material exceptions for the recorded reconciliation inputs."
    scope = "Reconciliation assesses independent source files. Verified M1–M5 calculations are unchanged."
    if latest["invoice_mode"] == "payment_rows":
        scope += " Invoice coverage is limited to face values recorded on payment rows."
    return {**latest, **counts, "state": status, "message": message, "scope": scope}
