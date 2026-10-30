"""add auditable recruiting pipeline, interview rounds, scorecards and offers

Revision ID: 20261031_0010
Revises: 20261030_0009
"""

import sqlalchemy as sa
from alembic import op

revision = "20261031_0010"
down_revision = "20261030_0009"
branch_labels = None
depends_on = None


TENANT_TABLES = (
    "application_audits",
    "interview_rounds",
    "interview_scorecards",
    "offers",
)


def _tenant_expression() -> str:
    return "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.add_column(
        "applications", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "applications",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_check_constraint(
        "ck_application_stage",
        "applications",
        "stage IN ('applied', 'screening', 'interview', 'offer', 'hired', 'rejected', 'withdrawn')",
    )
    op.create_index(
        "ix_applications_tenant_created_id_desc",
        "applications",
        ["tenant_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )

    op.create_table(
        "application_audits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("from_stage", sa.String(length=30), nullable=True),
        sa.Column("to_stage", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_application_audits_tenant_id", "application_audits", ["tenant_id"])
    op.create_index(
        "ix_application_audits_application_id", "application_audits", ["application_id"]
    )
    op.create_index("ix_application_audits_actor_id", "application_audits", ["actor_id"])

    op.create_table(
        "interview_rounds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("sequence > 0", name="ck_interview_round_sequence_positive"),
        sa.CheckConstraint(
            "duration_minutes BETWEEN 15 AND 480", name="ck_interview_round_duration"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "job_id", "sequence", name="uq_interview_round_sequence"),
    )
    op.create_index("ix_interview_rounds_tenant_id", "interview_rounds", ["tenant_id"])
    op.create_index("ix_interview_rounds_job_id", "interview_rounds", ["job_id"])

    op.add_column("interviews", sa.Column("application_id", sa.Uuid(), nullable=True))
    op.add_column("interviews", sa.Column("round_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_interviews_application_id", "interviews", "applications", ["application_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_interviews_round_id", "interviews", "interview_rounds", ["round_id"], ["id"]
    )
    op.create_index("ix_interviews_application_id", "interviews", ["application_id"])
    op.create_index("ix_interviews_round_id", "interviews", ["round_id"])

    op.create_table(
        "interview_scorecards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("interview_id", sa.Uuid(), sa.ForeignKey("interviews.id"), nullable=False),
        sa.Column("interviewer_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recommendation", sa.String(length=30), nullable=False),
        sa.Column("overall_score", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("overall_score BETWEEN 1 AND 5", name="ck_scorecard_overall_score"),
        sa.CheckConstraint(
            "recommendation IN ('strong_hire', 'hire', 'no_hire', 'strong_no_hire')",
            name="ck_scorecard_recommendation",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "interview_id", "interviewer_id", name="uq_interview_scorecard"
        ),
    )
    op.create_index("ix_interview_scorecards_tenant_id", "interview_scorecards", ["tenant_id"])
    op.create_index(
        "ix_interview_scorecards_interview_id", "interview_scorecards", ["interview_id"]
    )
    op.create_index(
        "ix_interview_scorecards_interviewer_id", "interview_scorecards", ["interviewer_id"]
    )

    op.create_table(
        "offers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("salary_amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("salary_amount >= 0", name="ck_offer_salary_nonnegative"),
        sa.CheckConstraint(
            "status IN ('draft', 'sent', 'accepted', 'declined', 'withdrawn')",
            name="ck_offer_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "application_id", name="uq_offer_application"),
    )
    op.create_index("ix_offers_tenant_id", "offers", ["tenant_id"])
    op.create_index("ix_offers_application_id", "offers", ["application_id"])
    op.create_index("ix_offers_status", "offers", ["status"])
    op.create_index("ix_offers_created_by", "offers", ["created_by"])

    expression = _tenant_expression()
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING (tenant_id = {expression}) WITH CHECK (tenant_id = {expression})"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO hireflow_app")


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")

    op.drop_table("offers")
    op.drop_table("interview_scorecards")
    op.drop_index("ix_interviews_round_id", table_name="interviews")
    op.drop_index("ix_interviews_application_id", table_name="interviews")
    op.drop_constraint("fk_interviews_round_id", "interviews", type_="foreignkey")
    op.drop_constraint("fk_interviews_application_id", "interviews", type_="foreignkey")
    op.drop_column("interviews", "round_id")
    op.drop_column("interviews", "application_id")
    op.drop_table("interview_rounds")
    op.drop_table("application_audits")
    op.drop_index("ix_applications_tenant_created_id_desc", table_name="applications")
    op.drop_constraint("ck_application_stage", "applications", type_="check")
    op.drop_column("applications", "updated_at")
    op.drop_column("applications", "version")
