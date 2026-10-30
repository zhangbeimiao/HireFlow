"""add database-protected offer status audit history

Revision ID: 20261031_0011
Revises: 20261031_0010
"""

import sqlalchemy as sa
from alembic import op

revision = "20261031_0011"
down_revision = "20261031_0010"
branch_labels = None
depends_on = None


def _tenant_expression() -> str:
    return "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "offer_audits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("offer_id", sa.Uuid(), sa.ForeignKey("offers.id"), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_offer_audits_tenant_id", "offer_audits", ["tenant_id"])
    op.create_index("ix_offer_audits_offer_id", "offer_audits", ["offer_id"])
    op.create_index("ix_offer_audits_actor_id", "offer_audits", ["actor_id"])
    expression = _tenant_expression()
    op.execute("ALTER TABLE offer_audits ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE offer_audits FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY offer_audits_tenant_isolation ON offer_audits "
        f"USING (tenant_id = {expression}) WITH CHECK (tenant_id = {expression})"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE offer_audits TO hireflow_app"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS offer_audits_tenant_isolation ON offer_audits")
    op.drop_table("offer_audits")
