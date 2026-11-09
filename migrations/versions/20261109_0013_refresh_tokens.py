"""add rotating refresh token sessions

Revision ID: 20261109_0013
Revises: 20261109_0012
"""

import sqlalchemy as sa
from alembic import op

revision = "20261109_0013"
down_revision = "20261109_0012"
branch_labels = None
depends_on = None


def _tenant_expression() -> str:
    return "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "replaced_by_id", sa.Uuid(), sa.ForeignKey("refresh_tokens.id"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_token_hash"),
    )
    op.create_index("ix_refresh_tokens_tenant_id", "refresh_tokens", ["tenant_id"])
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])
    expression = _tenant_expression()
    op.execute("ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE refresh_tokens FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY refresh_tokens_tenant_isolation ON refresh_tokens "
        f"USING (tenant_id = {expression}) WITH CHECK (tenant_id = {expression})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE refresh_tokens TO hireflow_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS refresh_tokens_tenant_isolation ON refresh_tokens")
    op.drop_table("refresh_tokens")
