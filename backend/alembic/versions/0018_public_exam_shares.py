"""Add public exam shares and public exam submissions tables."""

from alembic import op
import sqlalchemy as sa


revision = "0018_public_shares"
down_revision = "0017_listening_nav_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "public_exam_shares" not in tables:
        op.create_table(
            "public_exam_shares",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "exam_id",
                sa.String(length=36),
                sa.ForeignKey("exams.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column("share_code", sa.String(length=32), nullable=False, unique=True),
            sa.Column(
                "created_by_user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        op.create_index("ix_public_exam_shares_exam_id", "public_exam_shares", ["exam_id"])
        op.create_index("ix_public_exam_shares_share_code", "public_exam_shares", ["share_code"])
        op.create_index("ix_public_exam_shares_created_by_user_id", "public_exam_shares", ["created_by_user_id"])

    if "public_exam_submissions" not in tables:
        op.create_table(
            "public_exam_submissions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "share_id",
                sa.String(length=36),
                sa.ForeignKey("public_exam_shares.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "exam_id",
                sa.String(length=36),
                sa.ForeignKey("exams.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("student_name", sa.String(length=160), nullable=False),
            sa.Column("phone", sa.String(length=40), nullable=True, server_default=""),
            sa.Column("email", sa.String(length=320), nullable=True, server_default=""),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="in_progress"),
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("time_spent_seconds", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("total_correct", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("question_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("score_toeic", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("listening_score", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("reading_score", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("part_breakdown", sa.JSON(), nullable=True),
            sa.Column("answers", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
        op.create_index("ix_public_exam_submissions_share_id", "public_exam_submissions", ["share_id"])
        op.create_index("ix_public_exam_submissions_exam_id", "public_exam_submissions", ["exam_id"])
        op.create_index("ix_public_exam_submissions_status", "public_exam_submissions", ["status"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "public_exam_submissions" in tables:
        op.drop_table("public_exam_submissions")
    if "public_exam_shares" in tables:
        op.drop_table("public_exam_shares")
