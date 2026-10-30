"""add composite indexes for tenant keyset pagination

Revision ID: 20261029_0007
Revises: 20261029_0006
"""

from alembic import op

revision = "20261029_0007"
down_revision = "20261029_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_jobs_tenant_created_id_desc ON jobs (tenant_id, created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_candidates_tenant_created_id_desc "
        "ON candidates (tenant_id, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_candidates_tenant_created_id_desc")
    op.execute("DROP INDEX ix_jobs_tenant_created_id_desc")
