"""fence outbox relay leases with an owner token

Revision ID: 20261111_0017
Revises: 20261110_0015
"""

import sqlalchemy as sa
from alembic import op

revision = "20261111_0017"
down_revision = "20261110_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("lease_owner", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("outbox_events", "lease_owner")
