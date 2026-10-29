from datetime import datetime, timedelta

import pytest

from hireflow.domain import InterviewStatus, assert_valid_interval, can_transition, overlaps
from hireflow.outbox import retry_after
from hireflow.scheduling import Availability, earliest_common_slot, subtract_busy_slots


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_adjacent_interviews_do_not_overlap() -> None:
    assert not overlaps(at("2026-11-01T09:00:00+08:00"), at("2026-11-01T10:00:00+08:00"), at("2026-11-01T10:00:00+08:00"), at("2026-11-01T11:00:00+08:00"))


def test_overlapping_interviews_are_detected() -> None:
    assert overlaps(at("2026-11-01T09:00:00+08:00"), at("2026-11-01T10:00:00+08:00"), at("2026-11-01T09:30:00+08:00"), at("2026-11-01T10:30:00+08:00"))


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
