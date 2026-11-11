from sqlalchemy.exc import IntegrityError

from hireflow.service import _exclusion_conflict_message


class DriverIntegrityError(Exception):
    def __init__(self, sqlstate: str, constraint_name: str):
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


def _integrity_error(sqlstate: str, constraint_name: str) -> IntegrityError:
    return IntegrityError(
        "INSERT INTO interviews ...",
        {},
        DriverIntegrityError(sqlstate, constraint_name),
    )


def test_candidate_exclusion_violation_maps_to_schedule_conflict() -> None:
    message = _exclusion_conflict_message(
        _integrity_error("23P01", "interview_no_overlapping_candidate")
    )

    assert message == "candidate already has an overlapping active interview"


def test_unrelated_integrity_error_is_not_mapped_to_schedule_conflict() -> None:
    message = _exclusion_conflict_message(
        _integrity_error("23503", "fk_interviews_tenant_candidate")
    )

    assert message is None
