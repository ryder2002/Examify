"""Stable, URL-safe identifiers for exams."""

from __future__ import annotations

import re
import unicodedata
import uuid


def _ascii_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = normalized.replace("đ", "d").replace("Đ", "D")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")


def build_exam_slug(title: str, exam_id: str) -> str:
    """Use the exam UUID suffix so equal titles can never collide."""

    prefix = _ascii_slug(title)[:80].strip("-") or "de-thi"
    identifier = re.sub(r"[^a-f0-9]", "", str(exam_id).lower())
    return f"{prefix}-{identifier or uuid.uuid4().hex}"


def default_exam_slug() -> str:
    return f"de-thi-{uuid.uuid4().hex}"
