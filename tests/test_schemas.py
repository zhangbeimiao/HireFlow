import pytest
from pydantic import ValidationError

from hireflow.schemas import CandidateUpdate, JobUpdate


def test_patch_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError, match="at least one job field"):
        JobUpdate()
    with pytest.raises(ValidationError, match="at least one candidate field"):
        CandidateUpdate()


def test_patch_rejects_null_for_required_resource_fields() -> None:
    with pytest.raises(ValidationError, match="title cannot be null"):
        JobUpdate(title=None)
    with pytest.raises(ValidationError, match="email cannot be null"):
        CandidateUpdate(email=None)


def test_patch_allows_nullable_relationship_and_phone() -> None:
    assert JobUpdate(hiring_manager_id=None).model_dump(exclude_unset=True) == {
        "hiring_manager_id": None
    }
    assert CandidateUpdate(phone=None).model_dump(exclude_unset=True) == {"phone": None}
