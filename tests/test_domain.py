from datetime import datetime, timedelta

import pytest

from hireflow.domain import (
    ApplicationStage,
    InterviewStatus,
    OfferStatus,
    assert_valid_interval,
    can_transition,
    can_transition_application,
    can_transition_offer,
    overlaps,
)
from hireflow.outbox import retry_after
from hireflow.scheduling import Availability, earliest_common_slot, subtract_busy_slots


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_adjacent_interviews_do_not_overlap() -> None:
    assert not overlaps(
        at("2026-11-01T09:00:00+08:00"),
        at("2026-11-01T10:00:00+08:00"),
        at("2026-11-01T10:00:00+08:00"),
        at("2026-11-01T11:00:00+08:00"),
    )


def test_overlapping_interviews_are_detected() -> None:
    assert overlaps(
        at("2026-11-01T09:00:00+08:00"),
        at("2026-11-01T10:00:00+08:00"),
        at("2026-11-01T09:30:00+08:00"),
        at("2026-11-01T10:30:00+08:00"),
    )


def test_terminal_state_cannot_be_changed() -> None:
    assert not can_transition(InterviewStatus.COMPLETED, InterviewStatus.CANCELLED)
    assert can_transition(InterviewStatus.SCHEDULED, InterviewStatus.CONFIRMED)


def test_interview_needs_offset_aware_increasing_interval() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        assert_valid_interval(datetime(2026, 11, 1, 9), datetime(2026, 11, 1, 10))
    with pytest.raises(ValueError, match="before"):
        assert_valid_interval(at("2026-11-01T10:00:00+08:00"), at("2026-11-01T10:00:00+08:00"))


def test_scheduler_returns_earliest_shared_duration() -> None:
    slot = earliest_common_slot(
        [Availability(at("2026-11-01T10:00:00+08:00"), at("2026-11-01T12:00:00+08:00"))],
        [
            Availability(at("2026-11-01T09:00:00+08:00"), at("2026-11-01T10:30:00+08:00")),
            Availability(at("2026-11-01T11:00:00+08:00"), at("2026-11-01T12:00:00+08:00")),
        ],
        timedelta(minutes=30),
    )
    assert slot == Availability(at("2026-11-01T10:00:00+08:00"), at("2026-11-01T10:30:00+08:00"))


def test_outbox_backoff_is_exponential_and_bounded() -> None:
    assert retry_after(1) == timedelta(seconds=2)
    assert retry_after(4) == timedelta(seconds=16)
    assert retry_after(99) == timedelta(minutes=5)


def test_scheduler_removes_booked_interval_before_matching() -> None:
    remaining = subtract_busy_slots(
        [Availability(at("2026-11-01T09:00:00+08:00"), at("2026-11-01T12:00:00+08:00"))],
        [Availability(at("2026-11-01T10:00:00+08:00"), at("2026-11-01T11:00:00+08:00"))],
    )
    assert remaining == [
        Availability(at("2026-11-01T09:00:00+08:00"), at("2026-11-01T10:00:00+08:00")),
        Availability(at("2026-11-01T11:00:00+08:00"), at("2026-11-01T12:00:00+08:00")),
    ]


def test_scheduler_returns_none_when_availability_does_not_intersect() -> None:
    assert (
        earliest_common_slot(
            [Availability(at("2026-11-01T09:00:00+08:00"), at("2026-11-01T10:00:00+08:00"))],
            [Availability(at("2026-11-01T10:00:00+08:00"), at("2026-11-01T11:00:00+08:00"))],
            timedelta(minutes=30),
        )
        is None
    )


def test_scheduler_does_not_offer_a_slot_fully_consumed_by_busy_time() -> None:
    assert (
        subtract_busy_slots(
            [Availability(at("2026-11-01T09:00:00+08:00"), at("2026-11-01T10:00:00+08:00"))],
            [Availability(at("2026-11-01T08:30:00+08:00"), at("2026-11-01T10:30:00+08:00"))],
        )
        == []
    )


def test_cancellation_is_allowed_from_scheduled_state() -> None:
    assert can_transition(InterviewStatus.SCHEDULED, InterviewStatus.CANCELLED)


def test_application_pipeline_allows_only_forward_business_transitions() -> None:
    assert can_transition_application(ApplicationStage.APPLIED, ApplicationStage.SCREENING)
    assert can_transition_application(ApplicationStage.INTERVIEW, ApplicationStage.OFFER)
    assert can_transition_application(ApplicationStage.OFFER, ApplicationStage.HIRED)
    assert not can_transition_application(ApplicationStage.SCREENING, ApplicationStage.HIRED)
    assert not can_transition_application(ApplicationStage.HIRED, ApplicationStage.REJECTED)


def test_application_can_exit_from_each_active_stage() -> None:
    for stage in (
        ApplicationStage.APPLIED,
        ApplicationStage.SCREENING,
        ApplicationStage.INTERVIEW,
        ApplicationStage.OFFER,
    ):
        assert can_transition_application(stage, ApplicationStage.REJECTED)
        assert can_transition_application(stage, ApplicationStage.WITHDRAWN)


def test_offer_requires_send_before_candidate_decision() -> None:
    assert can_transition_offer(OfferStatus.DRAFT, OfferStatus.SENT)
    assert not can_transition_offer(OfferStatus.DRAFT, OfferStatus.ACCEPTED)
    assert can_transition_offer(OfferStatus.SENT, OfferStatus.ACCEPTED)
    assert can_transition_offer(OfferStatus.SENT, OfferStatus.DECLINED)
    assert not can_transition_offer(OfferStatus.ACCEPTED, OfferStatus.WITHDRAWN)
