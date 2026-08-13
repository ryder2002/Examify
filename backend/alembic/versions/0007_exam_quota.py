"""Add per-token and per-user exam limits."""

from alembic import op
import sqlalchemy as sa


revision = "0007_exam_quota"
down_revision = "0006_guides"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "exam_limit" not in _columns("users"):
        op.add_column("users", sa.Column("exam_limit", sa.Integer(), nullable=True))
    if "exam_created_count" not in _columns("users"):
        op.add_column(
            "users",
            sa.Column(
                "exam_created_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
        op.execute(
            sa.text(
                "UPDATE users SET exam_created_count = "
                "(SELECT COUNT(*) FROM exams "
                "WHERE exams.owner_user_id = users.id "
                "AND exams.deleted_at IS NULL)"
            )
        )
    if "exam_limit" not in _columns("activation_tokens"):
        op.add_column(
            "activation_tokens",
            sa.Column("exam_limit", sa.Integer(), nullable=False, server_default="5"),
        )


def downgrade() -> None:
    if "exam_limit" in _columns("activation_tokens"):
        op.drop_column("activation_tokens", "exam_limit")
    if "exam_limit" in _columns("users"):
        op.drop_column("users", "exam_limit")
    if "exam_created_count" in _columns("users"):
        op.drop_column("users", "exam_created_count")
