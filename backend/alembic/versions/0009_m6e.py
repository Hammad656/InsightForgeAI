"""Private connector configuration and synchronization provenance."""
from alembic import op

revision = "0009_m6e"
down_revision = "0008_m6d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE app.pg_connectors (
      org_id uuid NOT NULL REFERENCES app.organizations, id uuid NOT NULL, label text NOT NULL,
      profile text NOT NULL, enabled boolean NOT NULL DEFAULT true, removed boolean NOT NULL DEFAULT false,
      last_sync_at timestamptz, PRIMARY KEY(org_id,id)
    );
    CREATE TABLE app.pg_syncs (
      org_id uuid NOT NULL, id uuid NOT NULL, connector_id uuid NOT NULL,
      actor_id uuid NOT NULL REFERENCES app.users, request_key uuid NOT NULL, config jsonb NOT NULL,
      status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
      attempts integer NOT NULL DEFAULT 0, lease_token uuid, lease_until timestamptz,
      error_code text, row_count integer, version_id uuid, recon_source_id uuid,
      PRIMARY KEY(org_id,id), UNIQUE(org_id,connector_id,request_key),
      FOREIGN KEY(org_id,connector_id) REFERENCES app.pg_connectors,
      FOREIGN KEY(org_id,version_id) REFERENCES biz.dataset_versions,
      FOREIGN KEY(org_id,recon_source_id) REFERENCES app.recon_sources
    );
    CREATE INDEX pg_sync_history ON app.pg_syncs(org_id,connector_id,created_at DESC);
    """)
    for table in ("pg_connectors", "pg_syncs"):
        op.execute(f"""
          ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY tenant ON app.{table} USING (
            org_id=NULLIF(current_setting('insightforge.org',true),'')::uuid
          );
          GRANT SELECT,INSERT,UPDATE ON app.{table} TO insightforge_app;
        """)


def downgrade() -> None:
    op.execute("DROP TABLE app.pg_syncs, app.pg_connectors")
