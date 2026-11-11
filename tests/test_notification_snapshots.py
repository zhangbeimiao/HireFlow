from __future__ import annotations

import pytest

from hireflow.notifications import (
    InvalidEvent,
    _application_message,
    _message_for,
    _offer_message,
)


def test_interview_message_uses_event_snapshot() -> None:
    title, content = _message_for(
        "interview.scheduled",
        {"starts_at": "2026-11-12T10:00:00+08:00", "to_status": "scheduled"},
    )
    assert title == "面试已安排"
    assert "2026-11-12T10:00:00+08:00" in content


def test_status_messages_use_event_time_state() -> None:
    _, interview_content = _message_for(
        "interview.status_changed",
        {"to_status": "confirmed", "starts_at": "2026-11-12T11:00:00+08:00"},
    )
    _, application_content = _application_message(
        "application.stage_changed", {"to_stage": "interview"}
    )
    _, offer_content = _offer_message("offer.status_changed", {"to_status": "accepted"})
    assert "11:00:00" in interview_content
    assert application_content.endswith("interview")
    assert offer_content.endswith("accepted")


def test_snapshot_message_rejects_missing_required_state() -> None:
    with pytest.raises(InvalidEvent, match="starts_at"):
        _message_for("interview.scheduled", {})
