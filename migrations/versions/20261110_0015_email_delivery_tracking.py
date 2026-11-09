"""track email templates, provider failures, delivery and bounce events

Revision ID: 20261110_0015
Revises: 20261109_0014
"""

import sqlalchemy as sa
from alembic import op

revision = "20261110_0015"
down_revision = "20261109_0014"
branch_labels = None
depends_on = None


def _tenant_expression() -> str:
    return "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.add_column(
        "notification_deliveries",
        sa.Column(
            "template_version",
            sa.String(length=80),
            nullable=False,
            server_default="recruiting-notification.v1",
        ),
    )
    op.add_column(
        "notification_deliveries", sa.Column("failure_kind", sa.String(length=30))
    )
    op.add_column(
        "notification_deliveries", sa.Column("provider_status_code", sa.Integer())
    )
    op.add_column(
        "notification_deliveries", sa.Column("delivered_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "notification_deliveries", sa.Column("bounced_at", sa.DateTime(timezone=True))
    )
    op.add_column("notification_deliveries", sa.Column("bounce_reason", sa.Text()))

    op.create_table(
        "notification_delivery_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "delivery_id",
            sa.Uuid(),
            sa.ForeignKey("notification_deliveries.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_delivery_provider_event"),
    )
    op.create_index(
        "ix_notification_delivery_events_tenant_id",
        "notification_delivery_events",
        ["tenant_id"],
    )
    op.create_index(
        "ix_notification_delivery_events_delivery_id",
        "notification_delivery_events",
        ["delivery_id"],
    )
    expression = _tenant_expression()
    op.execute("ALTER TABLE notification_delivery_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notification_delivery_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY notification_delivery_events_tenant_isolation "
        "ON notification_delivery_events "
        f"USING (tenant_id = {expression}) WITH CHECK (tenant_id = {expression})"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE notification_delivery_events "
        "TO hireflow_app"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS notification_delivery_events_tenant_isolation "
        "ON notification_delivery_events"
    )
    op.drop_table("notification_delivery_events")
    op.drop_column("notification_deliveries", "bounce_reason")
    op.drop_column("notification_deliveries", "bounced_at")
    op.drop_column("notification_deliveries", "delivered_at")
    op.drop_column("notification_deliveries", "provider_status_code")
    op.drop_column("notification_deliveries", "failure_kind")
    op.drop_column("notification_deliveries", "template_version")
