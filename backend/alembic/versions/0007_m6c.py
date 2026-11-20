"""Tenant dashboards and private executive report schedules."""
from alembic import op

revision = "0007_m6c"
down_revision = "0006_m6b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE app.dashboards (
      org_id uuid REFERENCES app.organizations NOT NULL,
      id uuid NOT NULL, owner_id uuid REFERENCES app.users NOT NULL,
      title text NOT NULL, widgets jsonb NOT NULL DEFAULT '[]', revision integer NOT NULL DEFAULT 1,
      PRIMARY KEY(org_id,id)
    );
    CREATE TABLE app.dashboard_shares (
      org_id uuid NOT NULL, dashboard_id uuid NOT NULL, user_id uuid REFERENCES app.users NOT NULL,
      editor boolean NOT NULL DEFAULT false, PRIMARY KEY(org_id,dashboard_id,user_id),
      FOREIGN KEY(org_id,dashboard_id) REFERENCES app.dashboards ON DELETE CASCADE
    );
    CREATE TABLE app.dashboard_defaults (
      org_id uuid NOT NULL, user_id uuid REFERENCES app.users NOT NULL, dashboard_id uuid NOT NULL,
      PRIMARY KEY(org_id,user_id),
      FOREIGN KEY(org_id,dashboard_id) REFERENCES app.dashboards ON DELETE CASCADE
    );
    CREATE TABLE app.report_schedules (
      org_id uuid REFERENCES app.organizations NOT NULL, id uuid NOT NULL,
      owner_id uuid REFERENCES app.users NOT NULL, config jsonb NOT NULL,
      next_at timestamptz NOT NULL, paused boolean NOT NULL DEFAULT false,
      PRIMARY KEY(org_id,id)
    );
    CREATE TABLE app.report_schedule_runs (
      org_id uuid NOT NULL, schedule_id uuid NOT NULL, due_at timestamptz NOT NULL,
      report_id uuid, error_code text,
      PRIMARY KEY(org_id,schedule_id,due_at),
      FOREIGN KEY(org_id,schedule_id) REFERENCES app.report_schedules,
      FOREIGN KEY(org_id,report_id) REFERENCES app.reports
    );
    CREATE TABLE app.schedule_wakeups (
      org_id uuid NOT NULL, schedule_id uuid NOT NULL, next_at timestamptz NOT NULL,
      PRIMARY KEY(org_id,schedule_id),
      FOREIGN KEY(org_id,schedule_id) REFERENCES app.report_schedules ON DELETE CASCADE
    );
    CREATE INDEX schedule_wakeups_due ON app.schedule_wakeups(next_at,org_id,schedule_id);
    CREATE FUNCTION app.due_report_schedules()
    RETURNS TABLE(org_id uuid, schedule_id uuid)
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
      SELECT w.org_id,w.schedule_id FROM app.schedule_wakeups w
      WHERE w.next_at <= clock_timestamp() ORDER BY w.next_at,w.org_id,w.schedule_id LIMIT 50
    $$;
    REVOKE ALL ON FUNCTION app.due_report_schedules() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION app.due_report_schedules() TO insightforge_app;
    ALTER TABLE app.schedule_wakeups ENABLE ROW LEVEL SECURITY;
    CREATE POLICY tenant ON app.schedule_wakeups USING (
      org_id=NULLIF(current_setting('insightforge.org',true),'')::uuid
    );
    GRANT SELECT,INSERT,UPDATE,DELETE ON app.schedule_wakeups TO insightforge_app;
    """)
    for table in ("dashboards", "dashboard_shares", "dashboard_defaults", "report_schedules", "report_schedule_runs"):
        op.execute(f"""
          ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY tenant ON app.{table} USING (
            org_id=NULLIF(current_setting('insightforge.org',true),'')::uuid
          );
          GRANT SELECT,INSERT,UPDATE,DELETE ON app.{table} TO insightforge_app;
        """)


def downgrade() -> None:
    op.execute("""
      DROP FUNCTION app.due_report_schedules();
      DROP TABLE app.schedule_wakeups,app.report_schedule_runs,app.report_schedules,
                 app.dashboard_defaults,app.dashboard_shares,app.dashboards;
    """)
