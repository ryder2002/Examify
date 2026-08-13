"""Add a user-facing category to saved exams."""

from alembic import op
import sqlalchemy as sa


revision = "0003_exam_category"
down_revision = "0002_activation_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("exams")}
    if "category" in columns:
        return
    op.add_column(
        "exams",
        sa.Column("category", sa.String(length=120), nullable=False, server_default=""),
    )
    op.create_index("ix_exams_category", "exams", ["category"])


def downgrade() -> None:
    op.drop_index("ix_exams_category", table_name="exams")
    op.drop_column("exams", "category")
