"""Stable opaque cursors for keyset pagination."""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class InvalidCursor(ValueError):
    pass


@dataclass(frozen=True)
class PageCursor:
    created_at: datetime
    item_id: UUID


def encode_cursor(created_at: datetime, item_id: UUID) -> str:
    if created_at.tzinfo is None:
        raise ValueError("cursor timestamp must be timezone-aware")
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": str(item_id)},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(value: str) -> PageCursor:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding))
        if not isinstance(payload, dict) or set(payload) != {"created_at", "id"}:
            raise ValueError
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None:
            raise ValueError
        return PageCursor(created_at=created_at, item_id=UUID(payload["id"]))
    except (binascii.Error, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise InvalidCursor("invalid pagination cursor") from error
