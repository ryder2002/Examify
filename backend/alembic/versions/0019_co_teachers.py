"""Add classroom co-teachers table."""

from alembic import op
import sqlalchemy as sa


revision = "0019_co_teachers"
down_revision = "0018_public_shares"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "classroom_co_teachers" not in tables:
        op.create_table(
            "classroom_co_teachers",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "classroom_id",
                sa.String(length=36),
                sa.ForeignKey("classrooms.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "teacher_user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "invited_by_user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default="active",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "classroom_id", "teacher_user_id", name="uq_co_teacher"
            ),
        )
        op.create_index(
            "ix_co_teacher_classroom",
            "classroom_co_teachers",
            ["classroom_id"],
        )
        op.create_index(
            "ix_co_teacher_teacher",
            "classroom_co_teachers",
            ["teacher_user_id"],
        )
        op.create_index(
            "ix_co_teacher_status",
            "classroom_co_teachers",
            ["status"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "classroom_co_teachers" in tables:
        op.drop_table("classroom_co_teachers")
