"""Request transactions and transaction-local tenant context."""
from collections.abc import Iterator
from uuid import UUID

from sqlalchemy import Connection, text

from app.core.database import get_engine


def transaction() -> Iterator[Connection]:
    with get_engine().begin() as connection:
        yield connection


def set_user(connection: Connection, user_id: UUID) -> None:
    connection.execute(text("SELECT set_config('insightforge.user', :value, true)"), {"value": str(user_id)})


def set_org(connection: Connection, org_id: UUID) -> None:
    connection.execute(text("SELECT set_config('insightforge.org', :value, true)"), {"value": str(org_id)})
