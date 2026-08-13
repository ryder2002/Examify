"""Add indexes for attempt history, monitoring and timeout finalization."""

from alembic import op
import sqlalchemy as sa


revision = "0015_attempt_indexes"
down_revision = "0014_attempt_revision"
branch_labels = None
depends_on = None


INDEXES = {
    "ix_attempts_user_status_submitted": ["user_id", "status", "submitted_at"],
    "ix_attempts_assignment_started": ["class_assignment_id", "started_at"],
    "ix_attempts_in_progress_deadline": ["deadline_at"],
}


def upgrade() -> None:
    existing = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("attempts")
    }
    for name, columns in INDEXES.items():
        if name in existing:
            continue
        options = {}
        if name == "ix_attempts_in_progress_deadline":
            options["postgresql_where"] = sa.text(
                "status = 'in_progress' AND class_assignment_id IS NOT NULL"
            )
            options["sqlite_where"] = sa.text(
                "status = 'in_progress' AND class_assignment_id IS NOT NULL"
            )
        op.create_index(name, "attempts", columns, **options)


def downgrade() -> None:
    existing = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("attempts")
    }
    for name in reversed(tuple(INDEXES)):
        if name in existing:
            op.drop_index(name, table_name="attempts")
