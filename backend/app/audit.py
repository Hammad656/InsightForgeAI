"""Persist fixed event identifiers and trusted context without request content."""
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import Connection, insert

from app.report_models import AuditEvent


@dataclass
class AuditContext:
    correlation_id: UUID
    actor: UUID | None = None
    org: UUID | None = None


audit_context: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)


def correlation_id() -> UUID:
    context = audit_context.get()
    return context.correlation_id if context else uuid4()


def actor_context(actor: UUID) -> None:
    context = audit_context.get()
    if context:
        context.actor = actor


def org_context(org: UUID) -> None:
    context = audit_context.get()
    if context:
        context.org = org


def audit(
    db: Connection, event: str, *, actor: UUID | None = None, org: UUID | None = None,
    resource: UUID | None = None, correlation: UUID | None = None,
) -> None:
    context = audit_context.get()
    db.execute(insert(AuditEvent).values(
        id=uuid4(), org_id=org, actor_id=actor if actor is not None else (context.actor if context else None),
        event_type=event, resource_id=resource, correlation_id=correlation or correlation_id(),
    ))
