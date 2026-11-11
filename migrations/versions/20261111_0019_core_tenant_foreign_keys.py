"""enforce tenant-safe foreign keys for the scheduling aggregate

Revision ID: 20261111_0019
Revises: 20261111_0018
"""

from alembic import op

revision = "20261111_0019"
down_revision = "20261111_0018"
branch_labels = None
depends_on = None


PARENT_UNIQUES = (
    ("users", "uq_users_tenant_id"),
    ("jobs", "uq_jobs_tenant_id"),
    ("candidates", "uq_candidates_tenant_id"),
    ("applications", "uq_applications_tenant_id"),
    ("interview_rounds", "uq_interview_rounds_tenant_id"),
)

TENANT_FOREIGN_KEYS = (
    ("fk_jobs_tenant_hiring_manager", "jobs", "users", ["tenant_id", "hiring_manager_id"]),
    ("fk_applications_tenant_job", "applications", "jobs", ["tenant_id", "job_id"]),
    (
        "fk_applications_tenant_candidate",
        "applications",
        "candidates",
        ["tenant_id", "candidate_id"],
    ),
    (
        "fk_interview_rounds_tenant_job",
        "interview_rounds",
        "jobs",
        ["tenant_id", "job_id"],
    ),
    (
        "fk_interviews_tenant_candidate",
        "interviews",
        "candidates",
        ["tenant_id", "candidate_id"],
    ),
    (
        "fk_interviews_tenant_interviewer",
        "interviews",
        "users",
        ["tenant_id", "interviewer_id"],
    ),
    ("fk_interviews_tenant_job", "interviews", "jobs", ["tenant_id", "job_id"]),
    (
        "fk_interviews_tenant_application",
        "interviews",
        "applications",
        ["tenant_id", "application_id"],
    ),
    (
        "fk_interviews_tenant_round",
        "interviews",
        "interview_rounds",
        ["tenant_id", "round_id"],
    ),
)


def upgrade() -> None:
    for table, name in PARENT_UNIQUES:
        op.create_unique_constraint(name, table, ["tenant_id", "id"])
    for name, source, target, columns in TENANT_FOREIGN_KEYS:
        op.create_foreign_key(name, source, target, columns, ["tenant_id", "id"])


def downgrade() -> None:
    for name, source, _target, _columns in reversed(TENANT_FOREIGN_KEYS):
        op.drop_constraint(name, source, type_="foreignkey")
    for table, name in reversed(PARENT_UNIQUES):
        op.drop_constraint(name, table, type_="unique")
