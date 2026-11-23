"""Tenant-scoped benchmark runs and a stable per-suite baseline."""
from alembic import op

revision = "0008_m6d"
down_revision = "0007_m6c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE app.evaluation_runs (
      org_id uuid NOT NULL REFERENCES app.organizations,
      id uuid NOT NULL, actor_id uuid NOT NULL REFERENCES app.users,
      request_key uuid NOT NULL, suite text NOT NULL, model text NOT NULL,
      status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed')),
      manifest jsonb NOT NULL, results jsonb, scores jsonb, error_code text,
      attempts integer NOT NULL DEFAULT 0, lease_token uuid, lease_until timestamptz,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
      PRIMARY KEY(org_id,id), UNIQUE(org_id,actor_id,request_key)
    );
    CREATE INDEX evaluation_history ON app.evaluation_runs(org_id,created_at DESC,id DESC);
    CREATE TABLE app.evaluation_baselines (
      org_id uuid NOT NULL, suite text NOT NULL, run_id uuid NOT NULL,
      PRIMARY KEY(org_id,suite), FOREIGN KEY(org_id,run_id) REFERENCES app.evaluation_runs
    );
    """)
    for table in ("evaluation_runs", "evaluation_baselines"):
        op.execute(f"""
          ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY tenant ON app.{table} USING (
            org_id=NULLIF(current_setting('insightforge.org',true),'')::uuid
          );
          GRANT SELECT,INSERT,UPDATE ON app.{table} TO insightforge_app;
        """)


def downgrade() -> None:
    op.execute("DROP TABLE app.evaluation_baselines, app.evaluation_runs")
