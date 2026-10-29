from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import Response

from hireflow.api import _page_response, app
from hireflow.pagination import InvalidCursor, decode_cursor, encode_cursor


def test_cursor_round_trip_preserves_keyset_boundary() -> None:
    created_at = datetime(2026, 10, 29, 9, 30, 45, 123456, tzinfo=UTC)
    item_id = UUID("12345678-1234-5678-1234-567812345678")

    decoded = decode_cursor(encode_cursor(created_at, item_id))

    assert decoded.created_at == created_at
    assert decoded.item_id == item_id


@pytest.mark.parametrize(
    "value",
    [
        "not-base64!",
        "e30",  # {}
        "eyJjcmVhdGVkX2F0IjoiMjAyNi0xMC0yOVQwOTozMDowMCJ9",  # missing id
    ],
)
def test_invalid_cursor_is_rejected(value: str) -> None:
    with pytest.raises(InvalidCursor, match="invalid pagination cursor"):
        decode_cursor(value)


def test_naive_timestamp_cannot_be_encoded() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        encode_cursor(datetime(2026, 10, 29), UUID(int=1))


def test_same_timestamp_uses_id_as_cursor_tie_breaker() -> None:
    timestamp = datetime(2026, 10, 30, tzinfo=UTC)
    items = [
        {"id": str(UUID(int=value)), "created_at": timestamp.isoformat()} for value in (3, 2, 1)
    ]
    response = Response()

    page = _page_response(items, 2, response)
    cursor = decode_cursor(response.headers["X-Next-Cursor"])

    assert [item["id"] for item in page] == [str(UUID(int=3)), str(UUID(int=2))]
    assert cursor.created_at == timestamp
    assert cursor.item_id == UUID(int=2)


@pytest.mark.parametrize("items", [[], [{"id": str(UUID(int=1)), "created_at": "2026-10-30T00:00:00+00:00"}]])
def test_empty_or_exact_boundary_page_has_no_next_cursor(items: list[dict[str, str]]) -> None:
    response = Response()

    page = _page_response(items, 1, response)

    assert page == items
    assert response.headers["X-Page-Limit"] == "1"
    assert "X-Next-Cursor" not in response.headers


def test_page_response_supports_alternate_timestamp_field() -> None:
    starts_at = datetime(2026, 11, 1, tzinfo=UTC)
    items = [
        SimpleNamespace(id=UUID(int=2), starts_at=starts_at),
        SimpleNamespace(id=UUID(int=1), starts_at=starts_at),
    ]
    response = Response()

    page = _page_response(items, 1, response, timestamp_field="starts_at")
    cursor = decode_cursor(response.headers["X-Next-Cursor"])

    assert page == items[:1]
    assert cursor.created_at == starts_at
    assert cursor.item_id == UUID(int=2)


def test_openapi_enforces_list_limit_bounds() -> None:
    operation = app.openapi()["paths"]["/v1/jobs"]["get"]
    limit = next(parameter for parameter in operation["parameters"] if parameter["name"] == "limit")

    assert limit["schema"]["minimum"] == 1
    assert limit["schema"]["maximum"] == 100
