"""add composite indexes for interview and notification pagination

Revision ID: 20261030_0008
Revises: 20261029_0007
"""

from alembic import op

revision = "20261030_0008"
down_revision = "20261029_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_interviews_tenant_starts_id_desc "
        "ON interviews (tenant_id, starts_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_notifications_tenant_created_id_desc "
        "ON notifications (tenant_id, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_notifications_tenant_created_id_desc")
    op.execute("DROP INDEX ix_interviews_tenant_starts_id_desc")
