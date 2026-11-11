"""PostgreSQL-only tests for invariants that cannot be proven with SQLite."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

DATABASE_URL = os.getenv("HIREFLOW_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="set HIREFLOW_TEST_DATABASE_URL to test PostgreSQL constraints",
    ),
]


@pytest.fixture
def connection():
    connection = psycopg.connect(DATABASE_URL)
    try:
        yield connection
    finally:
        # Every test proves real constraints without leaving fixture data behind.
        connection.rollback()
        connection.close()


def _tenant_entities(connection, label: str):
    tenant_id = uuid4()
    interviewer_id = uuid4()
    second_interviewer_id = uuid4()
    candidate_id = uuid4()
    job_id = uuid4()
    connection.execute(
        "INSERT INTO tenants (id, name) VALUES (%s, %s)",
        (tenant_id, f"constraint-{label}-{tenant_id}"),
    )
    for user_id, suffix in (
        (interviewer_id, "one"),
        (second_interviewer_id, "two"),
    ):
        connection.execute(
            """INSERT INTO users
               (id, tenant_id, email, display_name, role)
               VALUES (%s, %s, %s, %s, 'interviewer')""",
            (user_id, tenant_id, f"{suffix}-{user_id}@example.test", suffix),
        )
    connection.execute(
        "INSERT INTO candidates (id, tenant_id, email, full_name) VALUES (%s, %s, %s, %s)",
        (candidate_id, tenant_id, f"{candidate_id}@example.test", label),
    )
    connection.execute(
        "INSERT INTO jobs (id, tenant_id, title) VALUES (%s, %s, %s)",
        (job_id, tenant_id, f"{label} engineer"),
    )
    return tenant_id, interviewer_id, second_interviewer_id, candidate_id, job_id


def test_candidate_cannot_be_double_booked_with_different_interviewers(connection) -> None:
    tenant_id, first_interviewer, second_interviewer, candidate_id, job_id = _tenant_entities(
        connection, "candidate-overlap"
    )
    starts_at = datetime.now(UTC) + timedelta(days=30)
    ends_at = starts_at + timedelta(hours=1)
    connection.execute(
        """INSERT INTO interviews
           (id, tenant_id, candidate_id, interviewer_id, job_id, starts_at, ends_at, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'scheduled')""",
        (uuid4(), tenant_id, candidate_id, first_interviewer, job_id, starts_at, ends_at),
    )

    with pytest.raises(psycopg.errors.ExclusionViolation), connection.transaction():
        connection.execute(
            """INSERT INTO interviews
                   (id, tenant_id, candidate_id, interviewer_id, job_id,
                    starts_at, ends_at, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'scheduled')""",
            (
                uuid4(),
                tenant_id,
                candidate_id,
                second_interviewer,
                job_id,
                starts_at + timedelta(minutes=15),
                ends_at + timedelta(minutes=15),
            ),
        )

    # The constraints use [start, end), so an adjacent interview remains valid.
    connection.execute(
        """INSERT INTO interviews
           (id, tenant_id, candidate_id, interviewer_id, job_id, starts_at, ends_at, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'scheduled')""",
        (
            uuid4(),
            tenant_id,
            candidate_id,
            second_interviewer,
            job_id,
            ends_at,
            ends_at + timedelta(hours=1),
        ),
    )


def test_core_foreign_keys_reject_cross_tenant_relations(connection) -> None:
    first = _tenant_entities(connection, "tenant-a")
    second = _tenant_entities(connection, "tenant-b")
    tenant_a, interviewer_a, _, candidate_a, job_a = first
    tenant_b, _, _, candidate_b, job_b = second

    with pytest.raises(psycopg.errors.ForeignKeyViolation), connection.transaction():
        connection.execute(
            """INSERT INTO applications
                   (id, tenant_id, job_id, candidate_id, stage, version)
                   VALUES (%s, %s, %s, %s, 'applied', 1)""",
            (uuid4(), tenant_a, job_b, candidate_a),
        )

    application_b = uuid4()
    connection.execute(
        """INSERT INTO applications
           (id, tenant_id, job_id, candidate_id, stage, version)
           VALUES (%s, %s, %s, %s, 'applied', 1)""",
        (application_b, tenant_b, job_b, candidate_b),
    )
    starts_at = datetime.now(UTC) + timedelta(days=60)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), connection.transaction():
        connection.execute(
            """INSERT INTO interviews
                   (id, tenant_id, candidate_id, interviewer_id, job_id, application_id,
                    starts_at, ends_at, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'scheduled')""",
            (
                uuid4(),
                tenant_a,
                candidate_a,
                interviewer_a,
                job_a,
                application_b,
                starts_at,
                starts_at + timedelta(hours=1),
            ),
        )


def test_outbox_lease_owner_fences_stale_worker(connection) -> None:
    tenant_id = uuid4()
    event_id = uuid4()
    stale_owner = uuid4()
    current_owner = uuid4()
    connection.execute(
        "INSERT INTO tenants (id, name) VALUES (%s, %s)",
        (tenant_id, f"lease-owner-{tenant_id}"),
    )
    connection.execute(
        """INSERT INTO outbox_events
           (id, tenant_id, aggregate_id, event_type, payload, status,
            attempts, lease_owner, lease_expires_at)
           VALUES (%s, %s, %s, 'test.event', '{}'::jsonb, 'publishing',
                   1, %s, now() + interval '60 seconds')""",
        (event_id, tenant_id, uuid4(), current_owner),
    )

    stale_update = connection.execute(
        """UPDATE outbox_events
           SET status = 'published', lease_owner = NULL, lease_expires_at = NULL
           WHERE id = %s AND status = 'publishing' AND lease_owner = %s""",
        (event_id, stale_owner),
    )
    assert stale_update.rowcount == 0

    current_update = connection.execute(
        """UPDATE outbox_events
           SET status = 'published', lease_owner = NULL, lease_expires_at = NULL
           WHERE id = %s AND status = 'publishing' AND lease_owner = %s""",
        (event_id, current_owner),
    )
    assert current_update.rowcount == 1
