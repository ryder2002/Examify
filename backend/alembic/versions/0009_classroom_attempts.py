"""Normalize existing practice assignments to unlimited attempts."""

from alembic import op


revision = "0009_classroom_attempts"
down_revision = "0008_classrooms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE class_assignments
        SET attempt_limit = NULL
        WHERE mode = 'practice'
        """
    )


def downgrade() -> None:
    # Unlimited practice is intentional and cannot be safely reconstructed.
    pass
