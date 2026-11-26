"""Track dataset archival and cancelled import requests."""
from alembic import op

revision = "0011_dataset_lifecycle"
down_revision = "0010_m7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE app.uploads ADD COLUMN preview_row_count integer;
        GRANT DELETE ON app.uploads TO insightforge_app;
        ALTER TABLE app.datasets ADD COLUMN lifecycle_changed_at timestamptz;
        ALTER TABLE app.import_jobs DROP CONSTRAINT import_jobs_status_check;
        ALTER TABLE app.import_jobs ADD CONSTRAINT import_jobs_status_check
            CHECK (status IN ('queued','failed','published','cancelled'));
        ALTER TABLE app.import_jobs DROP CONSTRAINT import_jobs_org_id_domain_fingerprint_key;
        CREATE UNIQUE INDEX import_jobs_live_fingerprint
            ON app.import_jobs (org_id,domain,fingerprint) WHERE status <> 'cancelled';
        CREATE INDEX import_jobs_upload ON app.import_jobs (org_id,upload_id);
        CREATE TABLE app.archived_dataset_versions (
            org_id uuid NOT NULL, version_id uuid NOT NULL,
            archived_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (org_id,version_id),
            FOREIGN KEY (org_id,version_id) REFERENCES biz.dataset_versions
        );
        ALTER TABLE app.archived_dataset_versions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE app.archived_dataset_versions FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON app.archived_dataset_versions
            USING (org_id = NULLIF(current_setting('insightforge.org',true),'')::uuid);
        GRANT SELECT,INSERT,DELETE ON app.archived_dataset_versions TO insightforge_app;
    """)


def downgrade() -> None:
    raise RuntimeError("Dataset lifecycle downgrade would discard archival and cancellation history")
