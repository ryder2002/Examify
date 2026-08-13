"""Add immutable question projection and idempotent attempt sync metadata."""

from __future__ import annotations

import json
from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


revision = "0020_attempt_sync"
down_revision = "0019_co_teachers"
branch_labels = None
depends_on = None


def _part_number(question_number: int) -> int | None:
    for part, start, end in (
        (1, 1, 6),
        (2, 7, 31),
        (3, 32, 70),
        (4, 71, 100),
        (5, 101, 130),
        (6, 131, 146),
        (7, 147, 200),
    ):
        if start <= question_number <= end:
            return part
    return None


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _backfill_projection(conn: Any) -> None:
    projection = sa.table(
        "exam_version_questions",
        sa.column("exam_version_id", sa.String(36)),
        sa.column("question_number", sa.Integer()),
        sa.column("part_number", sa.Integer()),
        sa.column("correct", sa.String(1)),
    )
    rows: list[dict[str, Any]] = []
    for version_id, raw_payload in conn.execute(
        sa.text("SELECT id, payload FROM exam_versions")
    ):
        unique_questions: dict[int, dict[str, Any]] = {}
        for question in _payload(raw_payload).get("questions") or []:
            if not isinstance(question, dict):
                continue
            try:
                number = int(question.get("number"))
            except (TypeError, ValueError):
                continue
            if number <= 0 or number in unique_questions:
                continue
            correct = str(question.get("correct") or "").strip().upper()
            unique_questions[number] = {
                "exam_version_id": version_id,
                "question_number": number,
                "part_number": _part_number(number),
                "correct": correct if correct in {"A", "B", "C", "D"} else None,
            }
        rows.extend(unique_questions.values())
        if len(rows) >= 1_000:
            _insert_projection_rows(conn, projection, rows)
            rows.clear()
    if rows:
        _insert_projection_rows(conn, projection, rows)


def _insert_projection_rows(conn: Any, projection: Any, rows: list[dict[str, Any]]) -> None:
    """Make the backfill restart-safe, including legacy create_all databases."""

    if conn.dialect.name == "postgresql":
        statement = postgresql_insert(projection).values(rows).on_conflict_do_nothing(
            index_elements=["exam_version_id", "question_number"]
        )
        conn.execute(statement)
        return
    if conn.dialect.name == "sqlite":
        statement = sqlite_insert(projection).values(rows).on_conflict_do_nothing(
            index_elements=["exam_version_id", "question_number"]
        )
        conn.execute(statement)
        return
    conn.execute(projection.insert(), rows)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "exam_version_questions" not in tables:
        op.create_table(
            "exam_version_questions",
            sa.Column(
                "exam_version_id",
                sa.String(length=36),
                sa.ForeignKey("exam_versions.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("question_number", sa.Integer(), primary_key=True),
            sa.Column("part_number", sa.Integer(), nullable=True),
            sa.Column("correct", sa.String(length=1), nullable=True),
            sa.CheckConstraint(
                "part_number IS NULL OR part_number BETWEEN 1 AND 7",
                name="ck_exam_version_question_part",
            ),
            sa.CheckConstraint(
                "correct IS NULL OR correct IN ('A', 'B', 'C', 'D')",
                name="ck_exam_version_question_correct",
            ),
        )
    # Migration 0001 in old installations used current SQLAlchemy metadata and
    # may therefore have created this table before revision 0020. Always run an
    # idempotent fill so those databases cannot end up with an empty projection.
    _backfill_projection(conn)

    attempt_columns = {
        column["name"] for column in sa.inspect(conn).get_columns("attempts")
    }
    additions = (
        ("submit_receipt_id", sa.String(length=36)),
        ("submit_idempotency_key", sa.String(length=80)),
        ("submitted_answer_hash", sa.String(length=64)),
    )
    missing = [(name, kind) for name, kind in additions if name not in attempt_columns]
    if missing:
        with op.batch_alter_table("attempts") as batch:
            for name, kind in missing:
                batch.add_column(sa.Column(name, kind, nullable=True))

    inspector = sa.inspect(conn)
    unique_names = {
        item.get("name") for item in inspector.get_unique_constraints("attempts")
    }
    if "uq_attempt_submit_receipt" not in unique_names:
        with op.batch_alter_table("attempts") as batch:
            batch.create_unique_constraint(
                "uq_attempt_submit_receipt", ["submit_receipt_id"]
            )

    index_names = {item["name"] for item in sa.inspect(conn).get_indexes("attempts")}
    if "ix_attempts_assignment_member_started" not in index_names:
        op.create_index(
            "ix_attempts_assignment_member_started",
            "attempts",
            ["class_assignment_id", "class_member_id", "started_at"],
        )

    tables = set(sa.inspect(conn).get_table_names())
    if "attempt_sync_batches" not in tables:
        op.create_table(
            "attempt_sync_batches",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "attempt_id",
                sa.String(length=36),
                sa.ForeignKey("attempts.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("batch_id", sa.String(length=36), nullable=False),
            sa.Column("changes_hash", sa.String(length=64), nullable=False),
            sa.Column("base_revision", sa.Integer(), nullable=False),
            sa.Column("accepted_revision", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "attempt_id", "batch_id", name="uq_attempt_sync_batch"
            ),
        )
        op.create_index(
            "ix_attempt_sync_batches_created_at",
            "attempt_sync_batches",
            ["created_at"],
        )

    if conn.dialect.name == "postgresql":
        # The left-most columns of the unique indexes already cover lookups by
        # attempt_id; retaining separate single-column indexes only amplifies
        # autosave writes.
        existing_answer_indexes = {
            item["name"] for item in sa.inspect(conn).get_indexes("attempt_answers")
        }
        if "ix_attempt_answers_attempt_id" in existing_answer_indexes:
            op.drop_index(
                "ix_attempt_answers_attempt_id", table_name="attempt_answers"
            )
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
        for table_name in ("attempts", "attempt_answers", "anti_cheat_events"):
            op.execute(
                f"ALTER TABLE {table_name} SET ("
                "autovacuum_vacuum_scale_factor = 0.05, "
                "autovacuum_analyze_scale_factor = 0.02)"
            )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())
    if "attempt_sync_batches" in tables:
        op.drop_table("attempt_sync_batches")

    index_names = {item["name"] for item in sa.inspect(conn).get_indexes("attempts")}
    if "ix_attempts_assignment_member_started" in index_names:
        op.drop_index("ix_attempts_assignment_member_started", table_name="attempts")

    attempt_columns = {
        column["name"] for column in sa.inspect(conn).get_columns("attempts")
    }
    with op.batch_alter_table("attempts") as batch:
        unique_names = {
            item.get("name")
            for item in sa.inspect(conn).get_unique_constraints("attempts")
        }
        if "uq_attempt_submit_receipt" in unique_names:
            batch.drop_constraint("uq_attempt_submit_receipt", type_="unique")
        for name in (
            "submitted_answer_hash",
            "submit_idempotency_key",
            "submit_receipt_id",
        ):
            if name in attempt_columns:
                batch.drop_column(name)

    if "exam_version_questions" in tables:
        op.drop_table("exam_version_questions")

    if conn.dialect.name == "postgresql":
        for table_name in ("attempts", "attempt_answers", "anti_cheat_events"):
            op.execute(
                f"ALTER TABLE {table_name} RESET ("
                "autovacuum_vacuum_scale_factor, "
                "autovacuum_analyze_scale_factor)"
            )
        answer_indexes = {
            item["name"] for item in sa.inspect(conn).get_indexes("attempt_answers")
        }
        if "ix_attempt_answers_attempt_id" not in answer_indexes:
            op.create_index(
                "ix_attempt_answers_attempt_id", "attempt_answers", ["attempt_id"]
            )
