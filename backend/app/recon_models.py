"""SQLAlchemy mappings for reconciliation history and exceptions."""
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReconciliationRun(Base):
    __tablename__ = "recon_runs"
    __table_args__ = {"schema": "app"}

    org_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[UUID]
    request_key: Mapped[UUID]
    correlation_id: Mapped[UUID]
    input_ids: Mapped[list[UUID]] = mapped_column(ARRAY(PG_UUID(as_uuid=True)))
    alias_ids: Mapped[list[UUID]] = mapped_column(ARRAY(PG_UUID(as_uuid=True)))
    as_of: Mapped[date]
    status: Mapped[str]
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReconciliationIssue(Base):
    __tablename__ = "recon_issues"
    __table_args__ = {"schema": "app"}

    org_id: Mapped[UUID] = mapped_column(primary_key=True)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID]
    rule: Mapped[str]
    severity: Mapped[str]
    refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    expected: Mapped[str | None]
    observed: Mapped[str | None]
    reviewer: Mapped[UUID | None]
    status: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
