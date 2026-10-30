"""rotate the API database role using an injected secret

Revision ID: 20261030_0009
Revises: 20261030_0008
"""

import os

from alembic import op
from sqlalchemy import text

revision = "20261030_0009"
down_revision = "20261030_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    password = os.getenv("HIREFLOW_APP_DATABASE_PASSWORD")
    if not password or len(password) < 16:
        raise RuntimeError("HIREFLOW_APP_DATABASE_PASSWORD must contain at least 16 characters")
    # Keep the secret out of the migration SQL string and SQLAlchemy statement logs.
    # PostgreSQL utility statements do not accept a bind parameter directly, so pass
    # it through a transaction-local setting and quote it server-side with format(%L).
    bind = op.get_bind()
    bind.execute(
        text("SELECT set_config('hireflow.app_role_password', :password, true)"),
        {"password": password},
    )
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format(
                'ALTER ROLE hireflow_app WITH LOGIN PASSWORD %L',
                current_setting('hireflow.app_role_password')
            );
        END
        $$
        """
    )


def downgrade() -> None:
    # Password rotation is intentionally irreversible; downgrades must never restore an old secret.
    pass
