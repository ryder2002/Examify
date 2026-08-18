"""Add browser client-extraction sessions and student history cursor index."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0026_client_extractions"
down_revision = "0025_tag_scoped_exam_titles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``0001_initial`` imports the current SQLAlchemy models for fresh
    # databases, while upgraded databases arrive here with the old schema.
    # Make this migration idempotent for both paths instead of assuming that a
    # legacy constraint/index always exists.
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    source_constraints = {
        item.get("name") for item in inspector.get_unique_constraints("exam_sources")
    }
    if "exam_sources_object_key_key" in source_constraints:
        op.drop_constraint("exam_sources_object_key_key", "exam_sources", type_="unique")

    job_columns = {item["name"] for item in inspector.get_columns("jobs")}
    additions = (
        ("ingest_mode", sa.String(length=24), False, "server_ocr"),
        ("client_request_id", sa.String(length=64), True, None),
        ("manifest_hash", sa.String(length=64), True, None),
        ("draft_revision", sa.Integer(), False, "0"),
        ("expires_at", sa.DateTime(timezone=True), True, None),
    )
    missing = [item for item in additions if item[0] not in job_columns]
    if missing:
        with op.batch_alter_table("jobs") as batch:
            for name, kind, nullable, default in missing:
                batch.add_column(
                    sa.Column(
                        name,
                        kind,
                        nullable=nullable,
                        server_default=sa.text(f"'{default}'") if default is not None else None,
                    )
                )

    index_names = {item.get("name") for item in inspector.get_indexes("jobs")}
    if "ix_jobs_ingest_mode" not in index_names:
        op.create_index("ix_jobs_ingest_mode", "jobs", ["ingest_mode"])
    if "uq_jobs_owner_client_request" not in index_names:
        op.create_index(
            "uq_jobs_owner_client_request",
            "jobs",
            ["owner_user_id", "client_request_id"],
            unique=True,
            postgresql_where=sa.text("client_request_id IS NOT NULL"),
        )
    if "ix_jobs_status_expires" not in index_names:
        op.create_index("ix_jobs_status_expires", "jobs", ["status", "expires_at"])

    attempt_indexes = {item.get("name") for item in inspector.get_indexes("attempts")}
    if "ix_attempts_student_submitted_cursor" not in attempt_indexes:
        op.create_index(
            "ix_attempts_student_submitted_cursor",
            "attempts",
            ["class_member_id", sa.text("submitted_at DESC"), sa.text("id DESC")],
            postgresql_where=sa.text("status = 'submitted'"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for name in (
        "ix_attempts_student_submitted_cursor",
    ):
        if name in {item.get("name") for item in inspector.get_indexes("attempts")}:
            op.drop_index(name, table_name="attempts")
    job_indexes = {item.get("name") for item in inspector.get_indexes("jobs")}
    for name in ("ix_jobs_status_expires", "uq_jobs_owner_client_request", "ix_jobs_ingest_mode"):
        if name in job_indexes:
            op.drop_index(name, table_name="jobs")
    job_columns = {item["name"] for item in inspector.get_columns("jobs")}
    with op.batch_alter_table("jobs") as batch:
        for name in ("expires_at", "draft_revision", "manifest_hash", "client_request_id", "ingest_mode"):
            if name in job_columns:
                batch.drop_column(name)
    source_constraints = {
        item.get("name") for item in inspector.get_unique_constraints("exam_sources")
    }
    if "exam_sources_object_key_key" not in source_constraints:
        op.create_unique_constraint(
            "exam_sources_object_key_key", "exam_sources", ["object_key"]
        )
