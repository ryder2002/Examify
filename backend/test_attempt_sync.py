import unittest
import uuid

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from attempt_sync import (
    AttemptBatchReuse,
    AttemptRevisionConflict,
    canonical_answers,
    sync_attempt_changes,
)
from database import Base
from models import Attempt


class AttemptSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.attempt = Attempt(
            exam_id=str(uuid.uuid4()),
            duration_seconds=3_600,
            time_left_seconds=3_600,
        )
        self.session.add(self.attempt)
        self.session.flush()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def sync(
        self,
        *,
        batch_id: str,
        base_revision: int,
        changes: dict[str, str | None],
    ):
        return sync_attempt_changes(
            self.session,
            self.attempt,
            batch_id=batch_id,
            base_revision=base_revision,
            raw_changes=changes,
            allowed_numbers={1, 2},
            time_left_seconds=3_500,
        )

    def test_duplicate_batch_is_idempotent_and_clear_is_durable(self) -> None:
        first_id = str(uuid.uuid4())
        first = self.sync(
            batch_id=first_id,
            base_revision=0,
            changes={"1": "a"},
        )
        self.assertEqual(first.accepted_revision, 1)
        self.assertEqual(canonical_answers(self.session, self.attempt.id), {1: "A"})

        duplicate = self.sync(
            batch_id=first_id,
            base_revision=0,
            changes={"1": "A"},
        )
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(self.attempt.answer_revision, 1)

        second = self.sync(
            batch_id=str(uuid.uuid4()),
            base_revision=1,
            changes={"1": None, "2": "B"},
        )
        self.assertEqual(second.accepted_revision, 2)
        self.assertEqual(canonical_answers(self.session, self.attempt.id), {2: "B"})

    def test_stale_revision_and_batch_payload_reuse_are_rejected(self) -> None:
        batch_id = str(uuid.uuid4())
        self.sync(batch_id=batch_id, base_revision=0, changes={"1": "A"})
        with self.assertRaises(AttemptRevisionConflict):
            self.sync(
                batch_id=str(uuid.uuid4()),
                base_revision=0,
                changes={"2": "B"},
            )
        with self.assertRaises(AttemptBatchReuse):
            self.sync(batch_id=batch_id, base_revision=0, changes={"1": "C"})

    def test_normal_sync_query_count_is_bounded(self) -> None:
        statements = 0

        def count_statement(*_args) -> None:
            nonlocal statements
            statements += 1

        event.listen(self.engine, "before_cursor_execute", count_statement)
        try:
            self.sync(
                batch_id=str(uuid.uuid4()),
                base_revision=0,
                changes={"1": "A", "2": "B"},
            )
            self.session.flush()
        finally:
            event.remove(self.engine, "before_cursor_execute", count_statement)

        # The HTTP class sync path adds one joined context query and one small
        # projection query, keeping the documented endpoint gate at <= 5.
        self.assertLessEqual(statements, 3)


if __name__ == "__main__":
    unittest.main()
