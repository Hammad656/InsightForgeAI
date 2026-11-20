"""Add author-scoped durable multi-domain analysis state."""
from alembic import op

revision = "0006_m6b"
down_revision = "0005_m6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE app.ai_analyses (
        org_id uuid NOT NULL REFERENCES app.organizations,
        author_id uuid NOT NULL REFERENCES app.users,
        id uuid NOT NULL, request_key uuid NOT NULL, correlation_id uuid NOT NULL,
        question text NOT NULL CHECK(length(question) BETWEEN 3 AND 500),
        dataset_ids uuid[] NOT NULL CHECK(cardinality(dataset_ids)<=3),
        sources jsonb NOT NULL, data_quality jsonb, business_day date NOT NULL,
        status text NOT NULL DEFAULT 'queued' CHECK(status IN
            ('queued','running','completed','partial','failed','clarification_required','cancelled','timed_out')),
        plan jsonb, tasks jsonb NOT NULL DEFAULT '{}', summary jsonb,
        planner_tokens integer NOT NULL DEFAULT 0 CHECK(planner_tokens>=0),
        created_at timestamptz NOT NULL DEFAULT now(),
        started_at timestamptz, deadline_at timestamptz, finished_at timestamptz,
        PRIMARY KEY(org_id,author_id,id), UNIQUE(org_id,author_id,request_key)
    );
    CREATE INDEX ai_analyses_history ON app.ai_analyses (org_id,author_id,created_at DESC,id DESC);
    ALTER TABLE app.ai_analyses ENABLE ROW LEVEL SECURITY;
    ALTER TABLE app.ai_analyses FORCE ROW LEVEL SECURITY;
    CREATE POLICY own_analyses ON app.ai_analyses USING (
        org_id=NULLIF(current_setting('insightforge.org',true),'')::uuid
        AND author_id=NULLIF(current_setting('insightforge.user',true),'')::uuid
    );
    GRANT SELECT,INSERT,UPDATE ON app.ai_analyses TO insightforge_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE app.ai_analyses")
