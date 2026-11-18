"""Initialize namespaces for application and business data."""
from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA app")
    op.execute("CREATE SCHEMA biz")


def downgrade() -> None:
    op.execute("DROP SCHEMA biz")
    op.execute("DROP SCHEMA app")
