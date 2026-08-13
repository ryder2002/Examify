"""Optimistic, idempotent delta synchronization for exam answers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from attempt_answers import (
    InvalidAttemptAnswer,
    bulk_apply_attempt_answer_changes,
    normalize_attempt_answer_changes,
)
from models import Attempt, AttemptAnswer, AttemptSyncBatch, utcnow, uuid4


class AttemptPresence(BaseModel):
    answered_count: int = Field(default=0, ge=0, le=200)
    current_question_number: int | None = Field(default=None, ge=1, le=200)
    is_fullscreen: bool | None = None
    visibility_state: Literal["visible", "hidden"] | None = None


class AttemptSyncRequest(BaseModel):
    batch_id: UUID
    base_revision: int = Field(ge=0)
    changes: dict[str, str | None] = Field(default_factory=dict, max_length=50)
    time_left_seconds: int = Field(ge=0)
    presence: AttemptPresence = Field(default_factory=AttemptPresence)


class AttemptRevisionConflict(RuntimeError):
    """The client based its delta on a stale canonical answer revision."""


class AttemptBatchReuse(RuntimeError):
    """A batch UUID was reused with different answer changes."""


@dataclass(frozen=True)
class SyncResult:
    accepted_revision: int
    accepted_batch_id: str
    duplicate: bool


def canonical_answers(session: Any, attempt_id: str) -> dict[int, str]:
    return {
        number: selected
        for number, selected in session.execute(
            select(AttemptAnswer.question_number, AttemptAnswer.selected).where(
                AttemptAnswer.attempt_id == attempt_id
            )
        )
    }


def _changes_hash(changes: Mapping[int, str | None]) -> str:
    encoded = json.dumps(
        {str(number): changes[number] for number in sorted(changes)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sync_attempt_changes(
    session: Any,
    attempt: Attempt,
    *,
    batch_id: str,
    base_revision: int,
    raw_changes: Mapping[str, str | None],
    allowed_numbers: Set[int],
    time_left_seconds: int,
) -> SyncResult:
    """Apply one delta while the caller holds the Attempt row lock.

    PostgreSQL remains the source of truth. The durable batch ledger makes a
    retry safe even when the first HTTP response was lost after commit.
    """

    try:
        changes = normalize_attempt_answer_changes(raw_changes, allowed_numbers)
    except InvalidAttemptAnswer:
        raise
    fingerprint = _changes_hash(changes)
    current_revision = int(attempt.answer_revision or 0)
    ledger_values = {
        "id": uuid4(),
        "attempt_id": attempt.id,
        "batch_id": batch_id,
        "changes_hash": fingerprint,
        "base_revision": base_revision,
        "accepted_revision": base_revision + 1,
        "created_at": utcnow(),
    }
    dialect = session.get_bind().dialect.name
    inserted = False
    if dialect == "postgresql":
        statement = postgresql_insert(AttemptSyncBatch).values(ledger_values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["attempt_id", "batch_id"]
        ).returning(AttemptSyncBatch.id)
        inserted = session.scalar(statement) is not None
    elif dialect == "sqlite":
        statement = sqlite_insert(AttemptSyncBatch).values(ledger_values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["attempt_id", "batch_id"]
        ).returning(AttemptSyncBatch.id)
        inserted = session.scalar(statement) is not None
    else:
        previous = session.scalar(
            select(AttemptSyncBatch).where(
                AttemptSyncBatch.attempt_id == attempt.id,
                AttemptSyncBatch.batch_id == batch_id,
            )
        )
        if previous is None:
            session.add(AttemptSyncBatch(**ledger_values))
            inserted = True

    if not inserted:
        previous = session.scalar(
            select(AttemptSyncBatch).where(
                AttemptSyncBatch.attempt_id == attempt.id,
                AttemptSyncBatch.batch_id == batch_id,
            )
        )
        if previous is None:
            raise RuntimeError("Không đọc được acknowledgement của batch")
        if previous.changes_hash != fingerprint:
            raise AttemptBatchReuse("batch_id đã được dùng với nội dung khác")
        return SyncResult(
            accepted_revision=previous.accepted_revision,
            accepted_batch_id=batch_id,
            duplicate=True,
        )

    if base_revision != current_revision:
        raise AttemptRevisionConflict(
            f"Revision client {base_revision} khác server {current_revision}"
        )

    next_revision = current_revision + 1
    bulk_apply_attempt_answer_changes(session, attempt.id, changes)
    attempt.time_left_seconds = min(
        attempt.time_left_seconds,
        time_left_seconds,
        attempt.duration_seconds,
    )
    attempt.answer_revision = next_revision
    attempt.updated_at = utcnow()
    return SyncResult(
        accepted_revision=next_revision,
        accepted_batch_id=batch_id,
        duplicate=False,
    )
