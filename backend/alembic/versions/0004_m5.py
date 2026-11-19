"""Add tenant report jobs, one-time downloads and append-only audit events."""
from alembic import op

revision = "0004_m5"
down_revision = "0003_m4"
branch_labels = None
depends_on = None

TENANT = "org_id = NULLIF(current_setting('insightforge.org', true), '')::uuid"
ACTOR = "actor_id = NULLIF(current_setting('insightforge.user', true), '')::uuid"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE app.reports (
        org_id uuid NOT NULL REFERENCES app.organizations,
        id uuid NOT NULL,
        created_by uuid NOT NULL REFERENCES app.users,
        request_key uuid NOT NULL,
        template text NOT NULL CHECK (template='monthly-performance'),
        filters jsonb NOT NULL,
        sources jsonb NOT NULL,
        query_id uuid,
        correlation_id uuid NOT NULL,
        status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','completed','failed')),
        attempts integer NOT NULL DEFAULT 0,
        run_token uuid,
        lease_until timestamptz,
        error_code text,
        artifact_sha256 varchar(64),
        artifact_size integer CHECK (artifact_size BETWEEN 1 AND 8388608),
        created_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz,
        expires_at timestamptz,
        purged_at timestamptz,
        PRIMARY KEY (org_id,id),
        UNIQUE (org_id,created_by,request_key),
        FOREIGN KEY (org_id,created_by,query_id) REFERENCES app.ai_queries (org_id,author_id,id),
        CHECK ((status='completed')=(artifact_sha256 IS NOT NULL AND artifact_size IS NOT NULL AND expires_at IS NOT NULL)),
        CHECK ((status='processing')=(run_token IS NOT NULL AND lease_until IS NOT NULL))
    );
    CREATE INDEX report_history ON app.reports (org_id,created_at DESC,id DESC);
    CREATE INDEX report_retention ON app.reports (org_id,expires_at) WHERE purged_at IS NULL;
    CREATE TABLE app.report_downloads (
        org_id uuid NOT NULL,
        report_id uuid NOT NULL,
        actor_id uuid NOT NULL REFERENCES app.users,
        token_hash varchar(64) PRIMARY KEY,
        expires_at timestamptz NOT NULL,
        FOREIGN KEY (org_id,report_id) REFERENCES app.reports (org_id,id)
    );
    CREATE INDEX report_download_expiration ON app.report_downloads (org_id,expires_at);
    CREATE TABLE app.audit_events (
        id uuid PRIMARY KEY,
        org_id uuid REFERENCES app.organizations,
        actor_id uuid REFERENCES app.users,
        event_type varchar(60) NOT NULL,
        resource_id uuid,
        correlation_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX audit_tenant_history ON app.audit_events (org_id,created_at DESC);
    GRANT SELECT,INSERT,UPDATE ON app.reports TO insightforge_app;
    GRANT SELECT,INSERT,DELETE ON app.report_downloads TO insightforge_app;
    GRANT INSERT ON app.audit_events TO insightforge_app;
    INSERT INTO app.audit_events (id,event_type,correlation_id)
        VALUES (gen_random_uuid(),'semantic.catalog_baseline_recorded',gen_random_uuid());
    """)
    for table in ("reports", "report_downloads", "audit_events"):
        op.execute(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant ON app.reports USING ({TENANT})")
    op.execute(f"CREATE POLICY owner ON app.report_downloads USING ({TENANT} AND {ACTOR})")
    op.execute(f"""CREATE POLICY event_insert ON app.audit_events FOR INSERT
        WITH CHECK (org_id IS NULL OR {TENANT})""")


def downgrade() -> None:
    op.execute("DROP TABLE app.report_downloads, app.reports, app.audit_events")
