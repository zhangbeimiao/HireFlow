from datetime import UTC, datetime
from uuid import UUID

import pytest

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
