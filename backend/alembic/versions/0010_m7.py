"""Tenant-owned forecast runs and versioned artifacts."""
from alembic import op

revision = "0010_m7"
down_revision = "0009_m6e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE app.forecast_runs (
      org_id uuid NOT NULL REFERENCES app.organizations,id uuid NOT NULL,
      author_id uuid NOT NULL REFERENCES app.users,request_key uuid NOT NULL,
      config jsonb NOT NULL,sources jsonb NOT NULL,quality jsonb,evidence jsonb,
      result jsonb,artifact jsonb,artifact_expires_at timestamptz,error_code text,
      status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed','insufficient_history','blocked')),
      attempts integer NOT NULL DEFAULT 0,lease_token uuid,lease_until timestamptz,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),finished_at timestamptz,
      PRIMARY KEY(org_id,id),UNIQUE(org_id,author_id,request_key)
    );
    CREATE INDEX forecast_history ON app.forecast_runs(org_id,created_at DESC);
    ALTER TABLE app.forecast_runs ENABLE ROW LEVEL SECURITY;
    ALTER TABLE app.forecast_runs FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant ON app.forecast_runs USING (
      org_id=NULLIF(current_setting('insightforge.org',true),'')::uuid
    );
    GRANT SELECT,INSERT,UPDATE ON app.forecast_runs TO insightforge_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE app.forecast_runs")
