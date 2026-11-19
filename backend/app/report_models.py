"""Database mappings for reports, download grants and audit events."""
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = {"schema": "app"}

    org_id: Mapped[UUID] = mapped_column(ForeignKey("app.organizations.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"))
    request_key: Mapped[UUID]
    template: Mapped[str]
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sources: Mapped[dict[str, Any]] = mapped_column(JSONB)
    query_id: Mapped[UUID | None]
    correlation_id: Mapped[UUID]
    status: Mapped[str]
    attempts: Mapped[int]
    run_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None]
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    artifact_size: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportDownload(Base):
    __tablename__ = "report_downloads"
    __table_args__ = (
        ForeignKeyConstraint(["org_id", "report_id"], ["app.reports.org_id", "app.reports.id"]),
        {"schema": "app"},
    )

    org_id: Mapped[UUID]
    report_id: Mapped[UUID]
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = {"schema": "app"}

    id: Mapped[UUID] = mapped_column(primary_key=True)
    org_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.organizations.id"))
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    event_type: Mapped[str] = mapped_column(String(60))
    resource_id: Mapped[UUID | None]
    correlation_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
