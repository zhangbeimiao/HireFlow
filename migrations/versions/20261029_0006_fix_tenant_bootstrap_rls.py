"""require RLS context while returning newly created tenant rows

Revision ID: 20261029_0006
Revises: 20261029_0005
"""

from alembic import op

revision = "20261029_0006"
down_revision = "20261029_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    expression = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("DROP POLICY tenants_tenant_isolation ON tenants")
    op.execute(
        f"CREATE POLICY tenants_tenant_isolation ON tenants USING (id = {expression}) "
        f"WITH CHECK (id = {expression})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY tenants_tenant_isolation ON tenants")
    op.execute(
        "CREATE POLICY tenants_tenant_isolation ON tenants "
        "USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) WITH CHECK (true)"
    )
