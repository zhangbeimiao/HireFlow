"""add refresh token families for replay containment

Revision ID: 20261109_0014
Revises: 20261109_0013
"""

import sqlalchemy as sa
from alembic import op

revision = "20261109_0014"
down_revision = "20261109_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("family_id", sa.Uuid(), nullable=True))
    op.execute("UPDATE refresh_tokens SET family_id = id WHERE family_id IS NULL")
    op.alter_column("refresh_tokens", "family_id", nullable=False)
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "family_id")
