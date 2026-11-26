"""Emit fixed fields for operational events without request or SQL content."""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from sqlalchemy import Engine, event


correlation: ContextVar[str | None] = ContextVar("operational_correlation", default=None)
logger = logging.getLogger("insightforge.telemetry")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)
logger.propagate = False


def emit(name: str, **fields: str | int | float | None) -> None:
    logger.info(json.dumps({
        "timestamp": datetime.now(UTC).isoformat(),
        "event": name,
        "correlation_id": correlation.get(),
        **fields,
    }, separators=(",", ":")))


def observe_database(engine: Engine) -> Engine:
    @event.listens_for(engine, "before_cursor_execute")
    def start(conn: Any, cursor: Any, statement: Any, parameters: Any, context: Any, executemany: Any) -> None:
        context.insightforge_started = perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def finish(conn: Any, cursor: Any, statement: Any, parameters: Any, context: Any, executemany: Any) -> None:
        emit("db.query", duration_ms=round((perf_counter() - context.insightforge_started) * 1000, 2))

    return engine
