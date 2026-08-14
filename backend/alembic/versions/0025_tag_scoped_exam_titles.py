"""Allow one teacher to reuse an exam title under different tags."""

from __future__ import annotations

import unicodedata

import sqlalchemy as sa
from alembic import op


revision = "0025_tag_scoped_exam_titles"
down_revision = "0024_teacher_scoped_exam_bank"
branch_labels = None
depends_on = None


def _name_key(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return " ".join(normalized.split()).casefold()


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, owner_user_id, title, category "
            "FROM exams "
            "WHERE library_scope = 'teacher_shared' "
            "AND owner_user_id IS NOT NULL "
            "AND shared_title_key IS NOT NULL"
        )
    ).mappings().all()
    for row in rows:
        title_key = _name_key(row["title"])
        if not title_key:
            continue
        scoped_key = (
            f"{row['owner_user_id']}:{_name_key(row['category'])}:{title_key}"
        )
        connection.execute(
            sa.text("UPDATE exams SET shared_title_key = :key WHERE id = :id"),
            {"key": scoped_key, "id": row["id"]},
        )


def downgrade() -> None:
    # The old global title namespace cannot represent two same-owner exams
    # that became valid after this migration. Keep the scoped opaque keys on a
    # rollback rather than risking data loss or a uniqueness collision.
    pass
