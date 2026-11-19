"""Add an isolated reporting schema and tenant/author-scoped query history."""
from alembic import op

revision = "0003_m4"
down_revision = "0002_m2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='insightforge_reporting') THEN
            CREATE ROLE insightforge_reporting NOLOGIN NOINHERIT;
        END IF;
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='insightforge_view_owner') THEN
            CREATE ROLE insightforge_view_owner NOLOGIN NOINHERIT;
        END IF;
        IF EXISTS (
            SELECT FROM pg_roles WHERE rolname IN ('insightforge_reporting','insightforge_view_owner')
            AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication
                 OR (rolname='insightforge_view_owner' AND rolcanlogin))
        ) OR EXISTS (
            SELECT FROM pg_auth_members WHERE member IN (
                SELECT oid FROM pg_roles WHERE rolname IN ('insightforge_reporting','insightforge_view_owner')
            )
        ) THEN
            RAISE EXCEPTION 'Reporting roles must be restricted';
        END IF;
    END $$;
    CREATE SCHEMA reporting;
    REVOKE ALL ON SCHEMA reporting FROM PUBLIC;
    GRANT USAGE ON SCHEMA reporting TO insightforge_reporting;
    GRANT USAGE, CREATE ON SCHEMA reporting TO insightforge_view_owner;
    GRANT USAGE ON SCHEMA app,biz TO insightforge_view_owner;
    GRANT SELECT ON app.datasets,biz.dataset_versions,biz.dataset_rows TO insightforge_view_owner;
    CREATE VIEW reporting.rows WITH (security_barrier=true) AS
        SELECT org_id,version_id,payload FROM biz.dataset_rows;
    CREATE VIEW reporting.sources WITH (security_barrier=true) AS
        SELECT d.org_id,d.domain,v.id AS version,v.published_at
        FROM app.datasets d JOIN biz.dataset_versions v
          ON (v.org_id,v.domain,v.id)=(d.org_id,d.domain,d.active_version);
    ALTER VIEW reporting.rows OWNER TO insightforge_view_owner;
    ALTER VIEW reporting.sources OWNER TO insightforge_view_owner;
    REVOKE CREATE ON SCHEMA reporting FROM insightforge_view_owner;
    GRANT SELECT ON reporting.rows,reporting.sources TO insightforge_reporting;

    CREATE TABLE app.ai_queries (
        org_id uuid NOT NULL REFERENCES app.organizations,
        author_id uuid NOT NULL REFERENCES app.users,
        id uuid NOT NULL,
        question text NOT NULL CHECK (length(question) BETWEEN 3 AND 500),
        result jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (org_id,author_id,id)
    );
    CREATE INDEX ai_queries_history ON app.ai_queries (org_id,author_id,created_at DESC,id DESC);
    ALTER TABLE app.ai_queries ENABLE ROW LEVEL SECURITY;
    ALTER TABLE app.ai_queries FORCE ROW LEVEL SECURITY;
    CREATE POLICY own_queries ON app.ai_queries USING (
        org_id = NULLIF(current_setting('insightforge.org',true),'')::uuid
        AND author_id = NULLIF(current_setting('insightforge.user',true),'')::uuid
    );
    GRANT SELECT,INSERT ON app.ai_queries TO insightforge_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE app.ai_queries; DROP VIEW reporting.sources, reporting.rows; DROP SCHEMA reporting")
