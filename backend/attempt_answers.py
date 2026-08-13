"""Shared validation and set-based persistence for attempt answers."""

from __future__ import annotations

from collections.abc import Mapping, Set
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from models import AttemptAnswer, utcnow, uuid4


ANSWER_LETTERS = frozenset({"A", "B", "C", "D"})


class InvalidAttemptAnswer(ValueError):
    """Raised when an answer does not belong to the immutable exam snapshot."""


def normalize_attempt_answers(
    raw_answers: Mapping[str, str], allowed_numbers: Set[int]
) -> dict[int, str]:
    normalized: dict[int, str] = {}
    for raw_number, raw_letter in raw_answers.items():
        try:
            number = int(raw_number)
        except (TypeError, ValueError) as exc:
            raise InvalidAttemptAnswer("Số câu không hợp lệ") from exc
        letter = str(raw_letter).upper()
        if number not in allowed_numbers or letter not in ANSWER_LETTERS:
            raise InvalidAttemptAnswer(f"Đáp án câu {number} không hợp lệ")
        normalized[number] = letter
    return normalized


def normalize_attempt_answer_changes(
    raw_changes: Mapping[str, str | None], allowed_numbers: Set[int]
) -> dict[int, str | None]:
    """Validate a bounded delta; ``None`` explicitly clears an answer."""

    normalized: dict[int, str | None] = {}
    for raw_number, raw_letter in raw_changes.items():
        try:
            number = int(raw_number)
        except (TypeError, ValueError) as exc:
            raise InvalidAttemptAnswer("Số câu không hợp lệ") from exc
        if number not in allowed_numbers:
            raise InvalidAttemptAnswer(f"Đáp án câu {number} không hợp lệ")
        if raw_letter is None:
            normalized[number] = None
            continue
        letter = str(raw_letter).upper()
        if letter not in ANSWER_LETTERS:
            raise InvalidAttemptAnswer(f"Đáp án câu {number} không hợp lệ")
        normalized[number] = letter
    return normalized


def bulk_upsert_attempt_answers(
    session: Any,
    attempt_id: str,
    answers: Mapping[int, str],
    *,
    answered_at: datetime | None = None,
    correct_by_number: Mapping[int, str | None] | None = None,
) -> None:
    """Upsert one answer batch without issuing a lookup for every question."""

    if not answers:
        return
    timestamp = answered_at or utcnow()
    rows = [
        {
            "id": uuid4(),
            "attempt_id": attempt_id,
            "question_number": number,
            "selected": letter,
            "is_correct": (
                letter == correct_by_number.get(number)
                if correct_by_number is not None
                and correct_by_number.get(number) is not None
                else None
            ),
            "answered_at": timestamp,
        }
        for number, letter in answers.items()
    ]
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(AttemptAnswer).values(rows)
        statement = statement.on_conflict_do_update(
            constraint="uq_attempt_answer",
            set_={
                "selected": statement.excluded.selected,
                "answered_at": statement.excluded.answered_at,
                "is_correct": statement.excluded.is_correct,
            },
            # Autosave sends the durable snapshot, so most rows are commonly
            # unchanged. Avoid producing WAL and row versions for those rows.
            where=or_(
                AttemptAnswer.selected != statement.excluded.selected,
                AttemptAnswer.is_correct.is_distinct_from(
                    statement.excluded.is_correct
                ),
            ),
        )
        session.execute(statement)
        return
    if dialect == "sqlite":
        statement = sqlite_insert(AttemptAnswer).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["attempt_id", "question_number"],
            set_={
                "selected": statement.excluded.selected,
                "answered_at": statement.excluded.answered_at,
                "is_correct": statement.excluded.is_correct,
            },
            where=or_(
                AttemptAnswer.selected != statement.excluded.selected,
                AttemptAnswer.is_correct.is_distinct_from(
                    statement.excluded.is_correct
                ),
            ),
        )
        session.execute(statement)
        return

    # Development fallback for another SQLAlchemy dialect. It still performs
    # one lookup for the whole batch rather than one lookup per answer.
    existing = {
        answer.question_number: answer
        for answer in session.scalars(
            select(AttemptAnswer).where(
                AttemptAnswer.attempt_id == attempt_id,
                AttemptAnswer.question_number.in_(answers),
            )
        )
    }
    for number, letter in answers.items():
        answer = existing.get(number)
        if answer is None:
            session.add(
                AttemptAnswer(
                    attempt_id=attempt_id,
                    question_number=number,
                    selected=letter,
                    answered_at=timestamp,
                )
            )
        else:
            answer.selected = letter
            answer.answered_at = timestamp
            answer.is_correct = (
                letter == correct_by_number.get(number)
                if correct_by_number is not None
                and correct_by_number.get(number) is not None
                else None
            )


def bulk_apply_attempt_answer_changes(
    session: Any,
    attempt_id: str,
    changes: Mapping[int, str | None],
    *,
    answered_at: datetime | None = None,
) -> None:
    """Apply a delta with at most one DELETE and one set-based UPSERT."""

    cleared = [number for number, letter in changes.items() if letter is None]
    selected = {
        number: letter for number, letter in changes.items() if letter is not None
    }
    if cleared:
        session.execute(
            delete(AttemptAnswer).where(
                AttemptAnswer.attempt_id == attempt_id,
                AttemptAnswer.question_number.in_(cleared),
            )
        )
    bulk_upsert_attempt_answers(
        session,
        attempt_id,
        selected,
        answered_at=answered_at,
    )


def replace_attempt_answers(
    session: Any,
    attempt_id: str,
    answers: Mapping[int, str],
    *,
    answered_at: datetime | None = None,
    correct_by_number: Mapping[int, str | None] | None = None,
) -> None:
    """Persist a canonical full snapshot, including cleared selections."""

    stale = delete(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt_id)
    if answers:
        stale = stale.where(AttemptAnswer.question_number.not_in(tuple(answers)))
    session.execute(stale)
    bulk_upsert_attempt_answers(
        session,
        attempt_id,
        answers,
        answered_at=answered_at,
        correct_by_number=correct_by_number,
    )


def attempt_answer_count(session: Any, attempt_id: str) -> int:
    return int(
        session.scalar(
            select(func.count(AttemptAnswer.id)).where(
                AttemptAnswer.attempt_id == attempt_id
            )
        )
        or 0
    )
