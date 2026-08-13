"""Scope shared-exam title keys and bank visibility lookups by teacher.

Revision ID: 0024_teacher_scoped_exam_bank
Revises: 0023_performance_hot_path
"""

from alembic import op


revision = "0024_teacher_scoped_exam_bank"
down_revision = "0023_performance_hot_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The current unique constraint remains valid; namespacing its stored key
    # allows Teacher A and Teacher B to use the same visible title safely.
    # PostgreSQL and SQLite both support the ANSI || concatenation operator.
    op.execute(
        "UPDATE exams "
        "SET shared_title_key = owner_user_id || ':' || shared_title_key "
        "WHERE library_scope = 'teacher_shared' "
        "AND owner_user_id IS NOT NULL "
        "AND shared_title_key IS NOT NULL "
        "AND shared_title_key NOT LIKE owner_user_id || ':%'"
    )
    # This index matches the correlated EXISTS predicate used to resolve a
    # student's accessible teacher owners without scanning all memberships.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_class_members_user_status_classroom "
        "ON class_members (user_id, status, classroom_id)"
    )


def downgrade() -> None:
    # Do not strip the prefix: after upgrade two teachers may legitimately have
    # identical visible titles, which cannot be represented by the old global
    # key. Leaving a unique opaque key is safe for a rollback.
    op.execute("DROP INDEX IF EXISTS ix_class_members_user_status_classroom")
