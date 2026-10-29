"""outbox retry and lease fields

Revision ID: 20261029_0002
Revises: 20261029_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0002"
down_revision = "20261029_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("outbox_events", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.create_index("ix_outbox_events_next_attempt_at", "outbox_events", ["next_attempt_at"])
    op.create_index("ix_outbox_events_lease_expires_at", "outbox_events", ["lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_outbox_events_lease_expires_at", table_name="outbox_events")
    op.drop_index("ix_outbox_events_next_attempt_at", table_name="outbox_events")
    op.drop_column("outbox_events", "lease_expires_at")
    op.drop_column("outbox_events", "next_attempt_at")
