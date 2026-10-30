"""initial HireFlow schema with interview overlap exclusion

Revision ID: 20261029_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261029_0001"
down_revision = None
branch_labels = None
depends_on = None


uuid = postgresql.UUID(as_uuid=True)
timestamp = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "tenants",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
    )
    op.create_table(
        "users",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_table(
        "jobs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("hiring_manager_id", uuid, sa.ForeignKey("users.id")),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
    )
    op.create_index("ix_jobs_tenant_id", "jobs", ["tenant_id"])
    op.create_table(
        "candidates",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(30)),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.UniqueConstraint("tenant_id", "email", name="uq_candidate_tenant_email"),
    )
    op.create_index("ix_candidates_tenant_id", "candidates", ["tenant_id"])
    op.create_table(
        "applications",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("job_id", uuid, sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("candidate_id", uuid, sa.ForeignKey("candidates.id"), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False, server_default="applied"),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.UniqueConstraint("tenant_id", "job_id", "candidate_id", name="uq_application"),
    )
    op.create_index("ix_applications_tenant_id", "applications", ["tenant_id"])
    op.create_table(
        "candidate_availabilities",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("candidate_id", uuid, sa.ForeignKey("candidates.id"), nullable=False),
        sa.Column("starts_at", timestamp, nullable=False),
        sa.Column("ends_at", timestamp, nullable=False),
    )
    op.create_table(
        "interviewer_availabilities",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("interviewer_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("starts_at", timestamp, nullable=False),
        sa.Column("ends_at", timestamp, nullable=False),
    )
    op.create_table(
        "interviews",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("candidate_id", uuid, sa.ForeignKey("candidates.id"), nullable=False),
        sa.Column("interviewer_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("job_id", uuid, sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("starts_at", timestamp, nullable=False),
        sa.Column("ends_at", timestamp, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("meeting_url", sa.String(500)),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
    )
    op.execute(
        "ALTER TABLE interviews ADD CONSTRAINT interview_no_overlapping_interviewer "
        "EXCLUDE USING gist (interviewer_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&) "
        "WHERE (status IN ('scheduled', 'confirmed'))"
    )
    op.create_table(
        "interview_audits",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("interview_id", uuid, sa.ForeignKey("interviews.id"), nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("aggregate_id", uuid, nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", timestamp, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
    )
    op.create_index("ix_outbox_events_tenant_id", "outbox_events", ["tenant_id"])
    op.create_index("ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"])
    op.create_index("ix_outbox_events_event_type", "outbox_events", ["event_type"])
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("interview_audits")
    op.drop_table("interviews")
    op.drop_table("interviewer_availabilities")
    op.drop_table("candidate_availabilities")
    op.drop_table("applications")
    op.drop_table("candidates")
    op.drop_table("jobs")
    op.drop_table("users")
    op.drop_table("tenants")
