"""Add identity, tenant policies and immutable import snapshots."""
from alembic import op

revision = "0002_m2"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None

ORG = "NULLIF(current_setting('insightforge.org', true), '')::uuid"
USER = "NULLIF(current_setting('insightforge.user', true), '')::uuid"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE app.users (
        id uuid PRIMARY KEY, email text NOT NULL UNIQUE, password_hash text NOT NULL
    );
    CREATE TABLE app.sessions (
        id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES app.users,
        access_hash text NOT NULL UNIQUE, refresh_hash text NOT NULL UNIQUE,
        access_until timestamptz NOT NULL, expires_at timestamptz NOT NULL,
        revoked_at timestamptz
    );
    CREATE TABLE app.organizations (
        id uuid PRIMARY KEY, name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE app.memberships (
        org_id uuid REFERENCES app.organizations, user_id uuid REFERENCES app.users,
        role text NOT NULL CHECK (role IN ('admin','analyst','viewer')),
        PRIMARY KEY (org_id,user_id)
    );
    CREATE TABLE app.invitations (
        org_id uuid REFERENCES app.organizations, email text NOT NULL,
        role text NOT NULL CHECK (role IN ('admin','analyst','viewer')),
        token_hash text NOT NULL, expires_at timestamptz NOT NULL, PRIMARY KEY (org_id,email)
    );
    CREATE TABLE app.uploads (
        org_id uuid REFERENCES app.organizations, id uuid, uploader uuid NOT NULL REFERENCES app.users,
        filename text NOT NULL, digest text NOT NULL, content bytea NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (org_id,id)
    );
    CREATE TABLE app.datasets (
        org_id uuid REFERENCES app.organizations,
        domain text CHECK (domain IN ('sales','inventory','payments')),
        active_version uuid, PRIMARY KEY (org_id,domain)
    );
    CREATE TABLE app.import_jobs (
        org_id uuid, id uuid, upload_id uuid NOT NULL, domain text NOT NULL,
        requested_by uuid NOT NULL REFERENCES app.users, mapping jsonb NOT NULL,
        fingerprint text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz, row_count integer, errors jsonb,
        status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','failed','published')),
        PRIMARY KEY (org_id,id), UNIQUE (org_id,domain,fingerprint),
        FOREIGN KEY (org_id,upload_id) REFERENCES app.uploads,
        FOREIGN KEY (org_id,domain) REFERENCES app.datasets
    );
    CREATE INDEX import_history ON app.import_jobs (org_id,created_at DESC);
    CREATE TABLE biz.dataset_versions (
        org_id uuid, id uuid, domain text NOT NULL, published_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (org_id,id), UNIQUE (org_id,domain,id),
        FOREIGN KEY (org_id,id) REFERENCES app.import_jobs,
        FOREIGN KEY (org_id,domain) REFERENCES app.datasets
    );
    ALTER TABLE app.datasets ADD FOREIGN KEY (org_id,domain,active_version)
        REFERENCES biz.dataset_versions (org_id,domain,id);
    CREATE TABLE biz.dataset_rows (
        org_id uuid, version_id uuid, source_key text, payload jsonb NOT NULL,
        PRIMARY KEY (org_id,version_id,source_key),
        FOREIGN KEY (org_id,version_id) REFERENCES biz.dataset_versions
    );
    DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'insightforge_app') THEN
            RAISE EXCEPTION 'Provision the restricted insightforge_app role before M2 migration';
        END IF;
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'insightforge_app'
                   AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb)) THEN
            RAISE EXCEPTION 'insightforge_app must be a restricted runtime role';
        END IF;
    END $$;
    GRANT USAGE ON SCHEMA app,biz TO insightforge_app;
    GRANT SELECT,INSERT ON app.users,app.organizations,app.uploads TO insightforge_app;
    GRANT SELECT,INSERT,UPDATE ON app.sessions,app.datasets,app.import_jobs TO insightforge_app;
    GRANT SELECT,INSERT,UPDATE,DELETE ON app.memberships,app.invitations TO insightforge_app;
    GRANT SELECT,INSERT ON biz.dataset_versions,biz.dataset_rows TO insightforge_app;
    """)
    for table, key in (
        ("app.organizations", "id"), ("app.memberships", "org_id"),
        ("app.invitations", "org_id"), ("app.uploads", "org_id"),
        ("app.datasets", "org_id"), ("app.import_jobs", "org_id"),
        ("biz.dataset_versions", "org_id"), ("biz.dataset_rows", "org_id"),
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant ON {table} USING ({key} = {ORG})")
    op.execute(f"CREATE POLICY own_membership ON app.memberships FOR SELECT USING (user_id = {USER})")
    op.execute(f"""
        CREATE POLICY own_organizations ON app.organizations FOR SELECT USING (
            EXISTS (SELECT 1 FROM app.memberships m WHERE m.org_id = organizations.id AND m.user_id = {USER})
        )
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE app.datasets DROP CONSTRAINT datasets_org_id_domain_active_version_fkey;
    DROP TABLE biz.dataset_rows, biz.dataset_versions, app.import_jobs, app.datasets,
        app.uploads, app.invitations, app.memberships, app.organizations, app.sessions, app.users;
    """)
