"""database-enforced tenant isolation with a least-privilege API role

Revision ID: 20261029_0005
Revises: 20261029_0004
"""

from alembic import op

revision = "20261029_0005"
down_revision = "20261029_0004"
branch_labels = None
depends_on = None


TENANT_TABLES = (
    "users",
    "jobs",
    "candidates",
    "applications",
    "candidate_availabilities",
    "interviewer_availabilities",
    "interviews",
    "outbox_events",
    "notification_inbox",
    "notifications",
)


def _tenant_expression() -> str:
    # Missing context evaluates to NULL, therefore denies access instead of raising a cast error.
    return "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    # Local Docker development credentials only. In production this role and its secret belong in
    # the platform's secret manager / migration bootstrap, not application source code.
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hireflow_app') THEN
                CREATE ROLE hireflow_app LOGIN PASSWORD 'hireflow_app_dev_only'
                    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
            END IF;
        END $$;
        """
    )
    # Relay and consumer process records across tenants. They use the existing service account;
    # browser-facing API requests do not.
    op.execute("ALTER ROLE hireflow BYPASSRLS")
    op.execute("GRANT CONNECT ON DATABASE hireflow TO hireflow_app")
    op.execute("GRANT USAGE ON SCHEMA public TO hireflow_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO hireflow_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE hireflow IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO hireflow_app"
    )

    expression = _tenant_expression()
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING (tenant_id = {expression}) WITH CHECK (tenant_id = {expression})"
        )

    # The API generates a tenant UUID and establishes that tenant context before INSERT. Existing
    # tenant rows are readable or writable only in the caller's context.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenants_tenant_isolation ON tenants USING (id = {expression}) "
        f"WITH CHECK (id = {expression})"
    )


def downgrade() -> None:
    for table in (*TENANT_TABLES, "tenants"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
