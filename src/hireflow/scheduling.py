"""Deterministic, explainable scheduling rules for the first dispatch strategy."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Availability:
    starts_at: datetime
    ends_at: datetime


def earliest_common_slot(
    candidate_slots: Iterable[Availability],
    interviewer_slots: Iterable[Availability],
    duration: timedelta,
) -> Availability | None:
    """Return the earliest feasible half-open slot; inputs may arrive unsorted."""
    if duration <= timedelta(0):
        raise ValueError("duration must be positive")

    candidates = sorted(candidate_slots, key=lambda slot: slot.starts_at)
    interviewers = sorted(interviewer_slots, key=lambda slot: slot.starts_at)
    candidate_index = interviewer_index = 0
    while candidate_index < len(candidates) and interviewer_index < len(interviewers):
        candidate = candidates[candidate_index]
        interviewer = interviewers[interviewer_index]
        start = max(candidate.starts_at, interviewer.starts_at)
        end = min(candidate.ends_at, interviewer.ends_at)
        if end - start >= duration:
            return Availability(starts_at=start, ends_at=start + duration)
        if candidate.ends_at <= interviewer.ends_at:
            candidate_index += 1
        else:
            interviewer_index += 1
    return None


def subtract_busy_slots(
    available_slots: Iterable[Availability], busy_slots: Iterable[Availability]
) -> list[Availability]:
    """Remove booked intervals from availability while preserving half-open boundaries."""
    result: list[Availability] = []
    for available in sorted(available_slots, key=lambda slot: slot.starts_at):
        fragments = [available]
        for busy in busy_slots:
            next_fragments: list[Availability] = []
            for fragment in fragments:
                if busy.ends_at <= fragment.starts_at or busy.starts_at >= fragment.ends_at:
                    next_fragments.append(fragment)
                    continue
                if fragment.starts_at < busy.starts_at:
                    next_fragments.append(Availability(fragment.starts_at, min(busy.starts_at, fragment.ends_at)))
                if busy.ends_at < fragment.ends_at:
                    next_fragments.append(Availability(max(busy.ends_at, fragment.starts_at), fragment.ends_at))
            fragments = next_fragments
        result.extend(fragments)
    return result
