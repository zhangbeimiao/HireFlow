"""Pure domain rules; independent of HTTP and database adapters."""

from datetime import datetime
from enum import StrEnum


class InterviewStatus(StrEnum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class ApplicationStage(StrEnum):
    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEW = "interview"
    OFFER = "offer"
    HIRED = "hired"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class OfferStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


APPLICATION_TRANSITIONS: dict[ApplicationStage, set[ApplicationStage]] = {
    ApplicationStage.APPLIED: {
        ApplicationStage.SCREENING,
        ApplicationStage.REJECTED,
        ApplicationStage.WITHDRAWN,
    },
    ApplicationStage.SCREENING: {
        ApplicationStage.INTERVIEW,
        ApplicationStage.REJECTED,
        ApplicationStage.WITHDRAWN,
    },
    ApplicationStage.INTERVIEW: {
        ApplicationStage.OFFER,
        ApplicationStage.REJECTED,
        ApplicationStage.WITHDRAWN,
    },
    ApplicationStage.OFFER: {
        ApplicationStage.HIRED,
        ApplicationStage.REJECTED,
        ApplicationStage.WITHDRAWN,
    },
    ApplicationStage.HIRED: set(),
    ApplicationStage.REJECTED: set(),
    ApplicationStage.WITHDRAWN: set(),
}

OFFER_TRANSITIONS: dict[OfferStatus, set[OfferStatus]] = {
    OfferStatus.DRAFT: {OfferStatus.SENT, OfferStatus.WITHDRAWN},
    OfferStatus.SENT: {
        OfferStatus.ACCEPTED,
        OfferStatus.DECLINED,
        OfferStatus.WITHDRAWN,
    },
    OfferStatus.ACCEPTED: set(),
    OfferStatus.DECLINED: set(),
    OfferStatus.WITHDRAWN: set(),
}


ACTIVE_STATUSES = {InterviewStatus.SCHEDULED, InterviewStatus.CONFIRMED}

ALLOWED_TRANSITIONS: dict[InterviewStatus, set[InterviewStatus]] = {
    InterviewStatus.PENDING: {InterviewStatus.SCHEDULED, InterviewStatus.CANCELLED},
    InterviewStatus.SCHEDULED: {InterviewStatus.CONFIRMED, InterviewStatus.CANCELLED},
    InterviewStatus.CONFIRMED: {
        InterviewStatus.COMPLETED,
        InterviewStatus.CANCELLED,
        InterviewStatus.NO_SHOW,
    },
    InterviewStatus.COMPLETED: set(),
    InterviewStatus.CANCELLED: set(),
    InterviewStatus.NO_SHOW: set(),
}


def assert_valid_interval(starts_at: datetime, ends_at: datetime) -> None:
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        raise ValueError("starts_at and ends_at must include a UTC offset")
    if starts_at >= ends_at:
        raise ValueError("starts_at must be before ends_at")


def overlaps(
    first_start: datetime, first_end: datetime, second_start: datetime, second_end: datetime
) -> bool:
    """Use half-open intervals: an interview ending at 10:00 does not block 10:00."""
    return first_start < second_end and second_start < first_end


def can_transition(current: InterviewStatus, target: InterviewStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def can_transition_application(current: ApplicationStage, target: ApplicationStage) -> bool:
    return target in APPLICATION_TRANSITIONS[current]


def can_transition_offer(current: OfferStatus, target: OfferStatus) -> bool:
    return target in OFFER_TRANSITIONS[current]
