"""Validation and merge helpers for immutable TOEIC solution entries."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Iterable


MAX_SOLUTION_FIELD_LENGTH = 12_000
MAX_SOLUTIONS_BYTES = 2 * 1024 * 1024


class SolutionValidationError(ValueError):
    def __init__(self, issues: list[dict[str, Any]]):
        self.issues = issues
        super().__init__("; ".join(str(issue.get("message") or issue) for issue in issues))


def normalized_name_key(value: str) -> str:
    """Return the stable DB uniqueness key for shared titles and tag names."""

    normalized = unicodedata.normalize("NFKC", value or "")
    return " ".join(normalized.split()).casefold()


def allowed_solution_groups(exam_type: str) -> list[list[int]]:
    groups: list[list[int]] = []
    if exam_type in {"listening", "combined"}:
        groups.extend([[number] for number in range(1, 32)])
        groups.extend([list(range(start, start + 3)) for start in range(32, 71, 3)])
        groups.extend([list(range(start, start + 3)) for start in range(71, 101, 3)])
    if exam_type in {"reading", "combined"}:
        groups.extend([[number] for number in range(101, 201)])
    return groups


def solution_key(question_numbers: Iterable[int]) -> str:
    numbers = list(question_numbers)
    if not numbers:
        return ""
    if len(numbers) == 1:
        return f"q-{numbers[0]}"
    return f"q-{numbers[0]}-{numbers[-1]}"


def parse_solution_number(value: Any) -> list[int]:
    """Parse human/PDF STT values without accepting arbitrary spans.

    PDF table extractors commonly preserve vertically stacked digits as
    ``1\n0\n1``. NFKC plus whitespace removal deliberately maps that cell to
    question 101; Vietnamese/English labels are removed before compacting.
    """

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u200b", "").replace("\ufeff", "").strip()
    text = re.sub(
        r"^(?:(?:questions?|q)|câu(?:\s+hỏi)?|số)\s*[.#:\-]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", "", text).replace("–", "-").replace("—", "-")
    text = text.rstrip(".:")
    match = re.fullmatch(r"(\d{1,3})(?:-(\d{1,3}))?", text)
    if not match:
        raise ValueError(f"STT không hợp lệ: {value!s}")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if end < start or end - start > 2:
        raise ValueError(f"Khoảng câu không hợp lệ: {value!s}")
    return list(range(start, end + 1))


def _plain_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    return text or None


def validate_solutions(
    entries: Iterable[dict[str, Any]],
    exam_type: str,
) -> list[dict[str, Any]]:
    allowed = {tuple(group) for group in allowed_solution_groups(exam_type)}
    seen_numbers: set[int] = set()
    normalized: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            issues.append({"index": index, "code": "invalid_entry", "message": "Dòng lời giải phải là object."})
            continue
        try:
            numbers = [int(number) for number in (raw.get("question_numbers") or [])]
        except (TypeError, ValueError):
            numbers = []
        if tuple(numbers) not in allowed:
            issues.append(
                {
                    "index": index,
                    "code": "invalid_range",
                    "question_numbers": numbers,
                    "message": "STT không thuộc đúng câu/nhóm TOEIC của phần đã chọn.",
                }
            )
            continue
        overlap = sorted(seen_numbers.intersection(numbers))
        if overlap:
            issues.append(
                {
                    "index": index,
                    "code": "overlap",
                    "question_numbers": overlap,
                    "message": f"Câu bị trùng hoặc chồng lấn: {overlap}.",
                }
            )
            continue

        transcript = _plain_text(raw.get("transcript"))
        explanation = _plain_text(raw.get("explanation"))
        translation = _plain_text(raw.get("translation"))
        for field, value in (
            ("transcript", transcript),
            ("explanation", explanation),
            ("translation", translation),
        ):
            if value is not None and len(value) > MAX_SOLUTION_FIELD_LENGTH:
                issues.append(
                    {
                        "index": index,
                        "code": "field_too_long",
                        "field": field,
                        "message": f"{field} vượt quá {MAX_SOLUTION_FIELD_LENGTH:,} ký tự.",
                    }
                )
        if not any((transcript, explanation, translation)):
            issues.append(
                {
                    "index": index,
                    "code": "empty_entry",
                    "message": "Lời giải phải có nội dung hoặc bản dịch.",
                }
            )
            continue
        if any(issue.get("index") == index for issue in issues):
            continue

        seen_numbers.update(numbers)
        normalized.append(
            {
                "key": solution_key(numbers),
                "question_numbers": numbers,
                "transcript": transcript,
                "explanation": explanation,
                "translation": translation or "",
            }
        )

    normalized.sort(key=lambda entry: entry["question_numbers"][0])
    if len(json.dumps(normalized, ensure_ascii=False).encode("utf-8")) > MAX_SOLUTIONS_BYTES:
        issues.append(
            {
                "code": "payload_too_large",
                "message": f"Tổng lời giải vượt quá {MAX_SOLUTIONS_BYTES // (1024 * 1024)} MiB.",
            }
        )
    if issues:
        raise SolutionValidationError(issues)
    return normalized


def merge_solutions(
    current: Iterable[dict[str, Any]],
    incoming: Iterable[dict[str, Any]],
    exam_type: str,
    *,
    replace_all: bool = False,
) -> list[dict[str, Any]]:
    validated_incoming = validate_solutions(incoming, exam_type)
    if replace_all:
        return validated_incoming
    validated_current = validate_solutions(current, exam_type)
    merged = {entry["key"]: entry for entry in validated_current}
    merged.update({entry["key"]: entry for entry in validated_incoming})
    return validate_solutions(merged.values(), exam_type)


def solution_coverage(entries: Iterable[dict[str, Any]]) -> tuple[int, int]:
    materialized = list(entries)
    return len(materialized), len(
        {number for entry in materialized for number in entry.get("question_numbers", [])}
    )
