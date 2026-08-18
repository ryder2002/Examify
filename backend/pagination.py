"""Opaque keyset cursors shared by attempt history endpoints."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone


def encode_submitted_cursor(submitted_at: datetime, row_id: str) -> str:
    aware = submitted_at if submitted_at.tzinfo else submitted_at.replace(tzinfo=timezone.utc)
    payload = json.dumps([aware.isoformat(), row_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_submitted_cursor(value: str) -> tuple[datetime, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(raw, list) or len(raw) != 2:
            raise ValueError
        submitted_at = datetime.fromisoformat(str(raw[0]))
        if submitted_at.tzinfo is None:
            submitted_at = submitted_at.replace(tzinfo=timezone.utc)
        row_id = str(raw[1])
        if not row_id or len(row_id) > 64:
            raise ValueError
        return submitted_at, row_id
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Cursor lịch sử không hợp lệ") from exc
