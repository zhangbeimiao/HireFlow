"""user password hashes for JWT login

Revision ID: 20261029_0003
Revises: 20261029_0002
"""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0003"
down_revision = "20261029_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable only to preserve records created before authentication was introduced.
    op.add_column("users", sa.Column("password_hash", sa.String(255)))


def downgrade() -> None:
    op.drop_column("users", "password_hash")
