"""Shared teacher exam bank, immutable attempt versions and solutions workflow."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid

from alembic import op
import sqlalchemy as sa


revision = "0021_shared_bank"
down_revision = "0020_attempt_sync"
branch_labels = None
depends_on = None


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").split()).casefold()


def _columns(conn: object, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(conn).get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    exam_columns = _columns(conn, "exams")
    exam_additions = (
        ("library_scope", sa.String(length=24), "'personal'"),
        ("content_revision", sa.Integer(), "1"),
        ("current_version_id", sa.String(length=36), None),
        ("shared_title_key", sa.String(length=1024), None),
        ("last_edited_by_user_id", sa.String(length=36), None),
        ("solution_entry_count", sa.Integer(), "0"),
        ("solution_question_count", sa.Integer(), "0"),
    )
    missing_exam = [item for item in exam_additions if item[0] not in exam_columns]
    if missing_exam:
        with op.batch_alter_table("exams") as batch:
            for name, kind, default in missing_exam:
                batch.add_column(
                    sa.Column(
                        name,
                        kind,
                        nullable=default is None,
                        server_default=sa.text(default) if default else None,
                    )
                )

    exam_constraints = {
        item.get("name") for item in sa.inspect(conn).get_unique_constraints("exams")
    }
    exam_fks = {
        item.get("name") for item in sa.inspect(conn).get_foreign_keys("exams")
    }
    with op.batch_alter_table("exams") as batch:
        if "uq_exams_shared_title_key" not in exam_constraints:
            batch.create_unique_constraint(
                "uq_exams_shared_title_key", ["shared_title_key"]
            )
        if "fk_exams_current_version_id" not in exam_fks:
            batch.create_foreign_key(
                "fk_exams_current_version_id",
                "exam_versions",
                ["current_version_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "fk_exams_last_edited_by_user_id" not in exam_fks:
            batch.create_foreign_key(
                "fk_exams_last_edited_by_user_id",
                "users",
                ["last_edited_by_user_id"],
                ["id"],
                ondelete="SET NULL",
            )

    for index_name, columns in (
        ("ix_exams_library_scope", ["library_scope"]),
        ("ix_exams_current_version_id", ["current_version_id"]),
        ("ix_exams_shared_title_key", ["shared_title_key"]),
        ("ix_exams_last_edited_by_user_id", ["last_edited_by_user_id"]),
    ):
        indexes = {item["name"] for item in sa.inspect(conn).get_indexes("exams")}
        if index_name not in indexes:
            op.create_index(index_name, "exams", columns)

    version_constraints = {
        item.get("name")
        for item in sa.inspect(conn).get_unique_constraints("exam_versions")
    }
    if "uq_exam_version_content_hash" not in version_constraints:
        # Older releases created a new snapshot for every assignment, so the
        # same immutable payload can legitimately appear more than once. Keep
        # those rows (assignments still reference them) while making their
        # legacy hashes distinct before adding the new reuse constraint.
        seen_versions: set[tuple[str, str]] = set()
        version_rows = conn.execute(
            sa.text(
                "SELECT id, source_exam_id, content_hash "
                "FROM exam_versions ORDER BY created_at, id"
            )
        )
        for version_id, source_exam_id, content_hash in version_rows:
            identity = (str(source_exam_id), str(content_hash))
            if identity in seen_versions:
                legacy_hash = hashlib.sha256(
                    f"{source_exam_id}:{content_hash}:legacy:{version_id}".encode()
                ).hexdigest()
                conn.execute(
                    sa.text(
                        "UPDATE exam_versions SET content_hash=:content_hash "
                        "WHERE id=:version_id"
                    ),
                    {"content_hash": legacy_hash, "version_id": version_id},
                )
            else:
                seen_versions.add(identity)
        with op.batch_alter_table("exam_versions") as batch:
            batch.create_unique_constraint(
                "uq_exam_version_content_hash", ["source_exam_id", "content_hash"]
            )

    attempt_columns = _columns(conn, "attempts")
    if "exam_version_id" not in attempt_columns:
        with op.batch_alter_table("attempts") as batch:
            batch.add_column(sa.Column("exam_version_id", sa.String(length=36)))
            batch.create_foreign_key(
                "fk_attempts_exam_version_id",
                "exam_versions",
                ["exam_version_id"],
                ["id"],
                ondelete="RESTRICT",
            )
        op.create_index("ix_attempts_exam_version_id", "attempts", ["exam_version_id"])
    attempt_indexes = {item["name"] for item in sa.inspect(conn).get_indexes("attempts")}
    if "ix_attempts_user_exam_status_started" not in attempt_indexes:
        op.create_index(
            "ix_attempts_user_exam_status_started",
            "attempts",
            ["user_id", "exam_id", "status", "started_at"],
        )

    submission_columns = _columns(conn, "public_exam_submissions")
    if "exam_version_id" not in submission_columns:
        with op.batch_alter_table("public_exam_submissions") as batch:
            batch.add_column(sa.Column("exam_version_id", sa.String(length=36)))
            batch.create_foreign_key(
                "fk_public_submission_exam_version_id",
                "exam_versions",
                ["exam_version_id"],
                ["id"],
                ondelete="RESTRICT",
            )
        op.create_index(
            "ix_public_exam_submissions_exam_version_id",
            "public_exam_submissions",
            ["exam_version_id"],
        )

    tag_columns = _columns(conn, "exam_tags")
    if "name_key" not in tag_columns:
        with op.batch_alter_table("exam_tags") as batch:
            batch.add_column(sa.Column("name_key", sa.String(length=512)))
        seen: set[str] = set()
        for tag_id, name in conn.execute(sa.text("SELECT id, name FROM exam_tags")):
            key = _key(name)
            if key in seen:
                suffix = hashlib.sha256(str(tag_id).encode()).hexdigest()[:8]
                key = f"{key[:111]}#{suffix}"
            seen.add(key)
            conn.execute(
                sa.text("UPDATE exam_tags SET name_key=:key WHERE id=:id"),
                {"key": key, "id": tag_id},
            )
        with op.batch_alter_table("exam_tags") as batch:
            batch.alter_column("name_key", nullable=False)
            batch.create_unique_constraint("uq_exam_tags_name_key", ["name_key"])
        op.create_index("ix_exam_tags_name_key", "exam_tags", ["name_key"])

    tables = set(sa.inspect(conn).get_table_names())
    if "exam_sources" not in tables:
        op.create_table(
            "exam_sources",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("exam_id", sa.String(length=36), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
            sa.Column("component", sa.String(length=20), nullable=False, server_default="main"),
            sa.Column("bucket", sa.String(length=80), nullable=False),
            sa.Column("object_key", sa.String(length=1024), nullable=False, unique=True),
            sa.Column("filename", sa.String(length=512), nullable=False),
            sa.Column("content_type", sa.String(length=160), nullable=False, server_default="application/pdf"),
            sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("sha256", sa.String(length=64)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("exam_id", "component", name="uq_exam_source_component"),
        )
        op.create_index("ix_exam_sources_exam_id", "exam_sources", ["exam_id"])
        op.create_index("ix_exam_sources_object_key", "exam_sources", ["object_key"])
        op.create_index("ix_exam_sources_sha256", "exam_sources", ["sha256"])

    if "exam_edit_sessions" not in tables:
        op.create_table(
            "exam_edit_sessions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("exam_id", sa.String(length=36), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
            sa.Column("editor_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("base_revision", sa.Integer(), nullable=False),
            sa.Column("job_ids", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_exam_edit_sessions_exam_id", "exam_edit_sessions", ["exam_id"])
        op.create_index("ix_exam_edit_sessions_editor_user_id", "exam_edit_sessions", ["editor_user_id"])
        op.create_index("ix_exam_edit_sessions_status", "exam_edit_sessions", ["status"])
        op.create_index("ix_exam_edit_sessions_expires_at", "exam_edit_sessions", ["expires_at"])
        op.create_index("ix_exam_edit_sessions_exam_status", "exam_edit_sessions", ["exam_id", "status"])
        op.create_index("ix_exam_edit_sessions_editor_status", "exam_edit_sessions", ["editor_user_id", "status"])

    if "solution_imports" not in tables:
        op.create_table(
            "solution_imports",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("owner_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("exam_type", sa.String(length=20), nullable=False),
            sa.Column("filename", sa.String(length=512), nullable=False),
            sa.Column("content_type", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("bucket", sa.String(length=80)),
            sa.Column("object_key", sa.String(length=1024)),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("issues", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text()),
            sa.Column("expires_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_solution_imports_owner_user_id", "solution_imports", ["owner_user_id"])
        op.create_index("ix_solution_imports_exam_type", "solution_imports", ["exam_type"])
        op.create_index("ix_solution_imports_object_key", "solution_imports", ["object_key"])
        op.create_index("ix_solution_imports_status", "solution_imports", ["status"])
        op.create_index("ix_solution_imports_expires_at", "solution_imports", ["expires_at"])
        op.create_index("ix_solution_import_owner_created", "solution_imports", ["owner_user_id", "created_at"])

    if "system_state" not in tables:
        op.create_table(
            "system_state",
            sa.Column("key", sa.String(length=80), primary_key=True),
            sa.Column("value", sa.String(length=255), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    if conn.execute(sa.text("SELECT value FROM system_state WHERE key='data_epoch'" )).first() is None:
        conn.execute(
            sa.text(
                "INSERT INTO system_state (key, value, updated_at) "
                "VALUES ('data_epoch', :value, CURRENT_TIMESTAMP)"
            ),
            {"value": str(uuid.uuid4())},
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())
    for table in ("solution_imports", "exam_edit_sessions", "exam_sources", "system_state"):
        if table in tables:
            op.drop_table(table)

    if "name_key" in _columns(conn, "exam_tags"):
        with op.batch_alter_table("exam_tags") as batch:
            batch.drop_column("name_key")
    if "exam_version_id" in _columns(conn, "public_exam_submissions"):
        with op.batch_alter_table("public_exam_submissions") as batch:
            batch.drop_column("exam_version_id")
    if "exam_version_id" in _columns(conn, "attempts"):
        with op.batch_alter_table("attempts") as batch:
            batch.drop_column("exam_version_id")

    version_constraints = {
        item.get("name")
        for item in sa.inspect(conn).get_unique_constraints("exam_versions")
    }
    if "uq_exam_version_content_hash" in version_constraints:
        with op.batch_alter_table("exam_versions") as batch:
            batch.drop_constraint("uq_exam_version_content_hash", type_="unique")

    with op.batch_alter_table("exams") as batch:
        for column in (
            "solution_question_count",
            "solution_entry_count",
            "last_edited_by_user_id",
            "shared_title_key",
            "current_version_id",
            "content_revision",
            "library_scope",
        ):
            if column in _columns(conn, "exams"):
                batch.drop_column(column)
