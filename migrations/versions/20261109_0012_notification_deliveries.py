"""persist outbound notification provider deliveries

Revision ID: 20261109_0012
Revises: 20261031_0011
"""

import sqlalchemy as sa
from alembic import op

revision = "20261109_0012"
down_revision = "20261031_0011"
branch_labels = None
depends_on = None


def _tenant_expression() -> str:
    return "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "notification_id", sa.Uuid(), sa.ForeignKey("notifications.id"), nullable=False
        ),
        sa.Column("channel", sa.String(length=20), nullable=False, server_default="email"),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="smtp"),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "notification_id", "channel", name="uq_notification_delivery_channel"
        ),
    )
    op.create_index(
        "ix_notification_deliveries_tenant_id", "notification_deliveries", ["tenant_id"]
    )
    op.create_index(
        "ix_notification_deliveries_notification_id",
        "notification_deliveries",
        ["notification_id"],
    )
    op.create_index(
        "ix_notification_deliveries_status", "notification_deliveries", ["status"]
    )
    op.create_index(
        "ix_notification_deliveries_next_attempt_at",
        "notification_deliveries",
        ["next_attempt_at"],
    )
    op.create_index(
        "ix_notification_deliveries_lease_expires_at",
        "notification_deliveries",
        ["lease_expires_at"],
    )
    expression = _tenant_expression()
    op.execute("ALTER TABLE notification_deliveries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notification_deliveries FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY notification_deliveries_tenant_isolation ON notification_deliveries "
        f"USING (tenant_id = {expression}) WITH CHECK (tenant_id = {expression})"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE notification_deliveries TO hireflow_app"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS notification_deliveries_tenant_isolation "
        "ON notification_deliveries"
    )
    op.drop_table("notification_deliveries")
