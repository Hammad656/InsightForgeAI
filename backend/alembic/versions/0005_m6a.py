"""Add immutable reconciliation inputs and tenant-scoped review history."""
from alembic import op

revision = "0005_m6a"
down_revision = "0004_m5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE app.recon_entities (
        org_id uuid NOT NULL REFERENCES app.organizations, id uuid NOT NULL,
        kind text NOT NULL CHECK (kind IN ('customer','product','branch','invoice','payment','transaction')),
        canonical_key text NOT NULL, PRIMARY KEY(org_id,id), UNIQUE(org_id,kind,canonical_key), UNIQUE(org_id,kind,id)
    );
    CREATE TABLE app.recon_aliases (
        org_id uuid NOT NULL, id uuid NOT NULL, source text NOT NULL, kind text NOT NULL, source_key text NOT NULL,
        revision integer NOT NULL CHECK (revision>0), entity_id uuid NOT NULL, actor_id uuid NOT NULL REFERENCES app.users,
        created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(org_id,id),
        UNIQUE(org_id,source,kind,source_key,revision),
        FOREIGN KEY(org_id,kind,entity_id) REFERENCES app.recon_entities(org_id,kind,id)
    );
    CREATE TABLE app.recon_sources (
        org_id uuid NOT NULL REFERENCES app.organizations, id uuid NOT NULL, source text NOT NULL,
        domain text NOT NULL CHECK (domain IN ('sales','inventory','invoices','payments')),
        revision integer NOT NULL CHECK (revision>0), filename text NOT NULL, digest text NOT NULL,
        original bytea NOT NULL, header jsonb NOT NULL, records jsonb NOT NULL, options jsonb NOT NULL,
        actor_id uuid NOT NULL REFERENCES app.users, created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(org_id,id), UNIQUE(org_id,source,domain,revision)
    );
    CREATE TABLE app.recon_runs (
        org_id uuid NOT NULL REFERENCES app.organizations, id uuid NOT NULL,
        actor_id uuid NOT NULL REFERENCES app.users, request_key uuid NOT NULL,
        correlation_id uuid NOT NULL, input_ids uuid[] NOT NULL, alias_ids uuid[] NOT NULL,
        as_of date NOT NULL, status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','completed','failed')),
        summary jsonb, created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
        PRIMARY KEY(org_id,id), UNIQUE(org_id,request_key)
    );
    CREATE INDEX recon_run_history ON app.recon_runs(org_id,created_at DESC,id DESC);
    CREATE TABLE app.recon_issues (
        org_id uuid NOT NULL, id uuid NOT NULL, run_id uuid NOT NULL, rule text NOT NULL,
        severity text NOT NULL CHECK(severity IN ('warning','material')), refs jsonb NOT NULL,
        expected text, observed text, reviewer uuid REFERENCES app.users,
        status text NOT NULL DEFAULT 'unresolved' CHECK(status IN ('unresolved','reviewed','resolved','acknowledged')),
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(org_id,id), FOREIGN KEY(org_id,run_id) REFERENCES app.recon_runs(org_id,id)
    );
    CREATE INDEX recon_issue_run ON app.recon_issues(org_id,run_id,severity,status,id);
    CREATE TABLE app.recon_reviews (
        org_id uuid NOT NULL, id uuid NOT NULL, issue_id uuid NOT NULL, actor_id uuid NOT NULL REFERENCES app.users,
        previous_status text NOT NULL, status text NOT NULL, note text NOT NULL, reviewer uuid REFERENCES app.users,
        correction_alias_id uuid, correlation_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(org_id,id), FOREIGN KEY(org_id,issue_id) REFERENCES app.recon_issues(org_id,id),
        FOREIGN KEY(org_id,correction_alias_id) REFERENCES app.recon_aliases(org_id,id)
    );
    GRANT SELECT,INSERT ON app.recon_entities,app.recon_aliases,app.recon_sources,app.recon_reviews TO insightforge_app;
    GRANT SELECT,INSERT,UPDATE ON app.recon_runs,app.recon_issues TO insightforge_app;
    """)
    for table in ("entities", "aliases", "sources", "runs", "issues", "reviews"):
        op.execute(f"ALTER TABLE app.recon_{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE app.recon_{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant ON app.recon_{table} USING (
            org_id=NULLIF(current_setting('insightforge.org',true),'')::uuid
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE app.recon_reviews, app.recon_issues, app.recon_runs, app.recon_sources, app.recon_aliases, app.recon_entities")
