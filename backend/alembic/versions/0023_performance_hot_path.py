"""Add indexes for concurrent attempt starts, history and asset lookup.

The existing attempt-answer unique index already covers the hot answer lookup;
this migration only adds indexes that correspond to observed production query
shapes and removes two exact duplicate non-unique indexes.
"""

from alembic import op


revision = "0023_performance_hot_path"
down_revision = "0022_exam_slug"
branch_labels = None
depends_on = None


_UPGRADE = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_attempt_active_personal "
    "ON attempts (user_id, exam_id) "
    "WHERE status = 'in_progress' AND class_assignment_id IS NULL "
    "AND user_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_attempts_user_history_cursor "
    "ON attempts (user_id, submitted_at DESC, id DESC) "
    "WHERE status = 'submitted' AND class_assignment_id IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_exam_version_assets_version_filename "
    "ON exam_version_assets (exam_version_id, filename)",
    "CREATE INDEX IF NOT EXISTS ix_activation_tokens_created_by_user_id "
    "ON activation_tokens (created_by_user_id)",
    "CREATE INDEX IF NOT EXISTS ix_activation_tokens_redeemed_by_device_id "
    "ON activation_tokens (redeemed_by_device_id)",
    "CREATE INDEX IF NOT EXISTS ix_activation_token_groups_created_by_user_id "
    "ON activation_token_groups (created_by_user_id)",
    # These non-unique indexes duplicate the unique-constraint indexes created
    # by revisions 0021 and 0022.
    "DROP INDEX IF EXISTS ix_exams_shared_title_key",
    "DROP INDEX IF EXISTS ix_exams_slug",
)

_DOWNGRADE = (
    "CREATE INDEX IF NOT EXISTS ix_exams_shared_title_key "
    "ON exams (shared_title_key)",
    "CREATE INDEX IF NOT EXISTS ix_exams_slug ON exams (slug)",
    "DROP INDEX IF EXISTS ix_activation_token_groups_created_by_user_id",
    "DROP INDEX IF EXISTS ix_activation_tokens_redeemed_by_device_id",
    "DROP INDEX IF EXISTS ix_activation_tokens_created_by_user_id",
    "DROP INDEX IF EXISTS ix_exam_version_assets_version_filename",
    "DROP INDEX IF EXISTS ix_attempts_user_history_cursor",
    "DROP INDEX IF EXISTS uq_attempt_active_personal",
)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
