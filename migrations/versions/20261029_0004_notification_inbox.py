"""notification inbox and in-app notification ledger

Revision ID: 20261029_0004
Revises: 20261029_0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261029_0004"
down_revision = "20261029_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "notification_inbox",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("consumer_name", sa.String(80), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="processing"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("processed_at", timestamp),
        sa.UniqueConstraint("consumer_name", "event_id", name="uq_notification_inbox_event"),
    )
    op.create_index("ix_notification_inbox_tenant_id", "notification_inbox", ["tenant_id"])
    op.create_index("ix_notification_inbox_event_id", "notification_inbox", ["event_id"])
    op.create_index("ix_notification_inbox_status", "notification_inbox", ["status"])
    op.create_table(
        "notifications",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("source_event_id", sa.String(36), nullable=False),
        sa.Column("recipient_type", sa.String(20), nullable=False),
        sa.Column("recipient_id", uuid, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("read_at", timestamp),
        sa.UniqueConstraint(
            "source_event_id", "recipient_type", "recipient_id", name="uq_notification_recipient_event"
        ),
    )
    op.create_index("ix_notifications_tenant_id", "notifications", ["tenant_id"])
    op.create_index("ix_notifications_source_event_id", "notifications", ["source_event_id"])
    op.create_index("ix_notifications_recipient_id", "notifications", ["recipient_id"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("notification_inbox")
