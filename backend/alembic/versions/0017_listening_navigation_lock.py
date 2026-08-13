"""Persist the teacher-controlled Listening navigation policy."""

from alembic import op
import sqlalchemy as sa


revision = "0017_listening_nav_lock"
down_revision = "0016_desktop_manifest_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("class_assignments")
    }
    if "listening_navigation_locked" not in columns:
        op.add_column(
            "class_assignments",
            sa.Column(
                "listening_navigation_locked",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )
        op.alter_column(
            "class_assignments",
            "listening_navigation_locked",
            server_default=None,
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("class_assignments")
    }
    if "listening_navigation_locked" in columns:
        op.drop_column("class_assignments", "listening_navigation_locked")
