import pytest
from pydantic import ValidationError

from hireflow.schemas import (
    CandidateUpdate,
    InterviewRoundCreate,
    JobUpdate,
    OfferCreate,
    ScorecardUpsert,
)


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


def test_interview_round_rejects_unreasonable_duration() -> None:
    with pytest.raises(ValidationError):
        InterviewRoundCreate(name="Technical", sequence=1, duration_minutes=5)


def test_scorecard_score_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ScorecardUpsert(recommendation="hire", overall_score=6)


def test_offer_uses_nonnegative_smallest_currency_unit_and_iso_currency() -> None:
    with pytest.raises(ValidationError):
        OfferCreate(application_id="00000000-0000-0000-0000-000000000001", salary_amount=-1, currency="CNY")
    with pytest.raises(ValidationError):
        OfferCreate(application_id="00000000-0000-0000-0000-000000000001", salary_amount=100, currency="cny")
