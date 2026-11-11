"""prevent interviewer and candidate schedule overlaps

Revision ID: 20261111_0018
Revises: 20261111_0017
"""

from alembic import op

revision = "20261111_0018"
down_revision = "20261111_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tenant is part of both constraints so the scheduling invariant is explicit even
    # though entity UUIDs are globally unique today. Half-open ranges allow adjacent
    # interviews such as 10:00-11:00 and 11:00-12:00.
    op.execute("ALTER TABLE interviews DROP CONSTRAINT interview_no_overlapping_interviewer")
    op.execute(
        "ALTER TABLE interviews ADD CONSTRAINT interview_no_overlapping_interviewer "
        "EXCLUDE USING gist (tenant_id WITH =, interviewer_id WITH =, "
        "tstzrange(starts_at, ends_at, '[)') WITH &&) "
        "WHERE (status IN ('scheduled', 'confirmed'))"
    )
    op.execute(
        "ALTER TABLE interviews ADD CONSTRAINT interview_no_overlapping_candidate "
        "EXCLUDE USING gist (tenant_id WITH =, candidate_id WITH =, "
        "tstzrange(starts_at, ends_at, '[)') WITH &&) "
        "WHERE (status IN ('scheduled', 'confirmed'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE interviews DROP CONSTRAINT interview_no_overlapping_candidate")
    op.execute("ALTER TABLE interviews DROP CONSTRAINT interview_no_overlapping_interviewer")
    op.execute(
        "ALTER TABLE interviews ADD CONSTRAINT interview_no_overlapping_interviewer "
        "EXCLUDE USING gist (interviewer_id WITH =, "
        "tstzrange(starts_at, ends_at, '[)') WITH &&) "
        "WHERE (status IN ('scheduled', 'confirmed'))"
    )
