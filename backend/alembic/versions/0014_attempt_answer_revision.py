"""Add a monotonic acknowledgement revision to attempts."""

from alembic import op
import sqlalchemy as sa


revision = "0014_attempt_revision"
down_revision = "0013_permanent_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("attempts")
    }
    if "answer_revision" not in columns:
        op.add_column(
            "attempts",
            sa.Column(
                "answer_revision",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("attempts")
    }
    if "answer_revision" in columns:
        op.drop_column("attempts", "answer_revision")
