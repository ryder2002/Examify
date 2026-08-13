"""Add stable exam slugs.

Revision ID: 0022_exam_slug
Revises: 0021_shared_bank
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_exam_slug"
down_revision = "0021_shared_bank"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    columns = {column["name"] for column in sa.inspect(conn).get_columns("exams")}
    if "slug" not in columns:
        with op.batch_alter_table("exams") as batch:
            batch.add_column(sa.Column("slug", sa.String(length=120), nullable=True))
    conn.execute(
        sa.text(
            "UPDATE exams SET slug = 'de-thi-' || replace(id, '-', '') "
            "WHERE slug IS NULL OR trim(slug) = ''"
        )
    )
    constraints = {
        item.get("name") for item in sa.inspect(conn).get_unique_constraints("exams")
    }
    indexes = {item.get("name") for item in sa.inspect(conn).get_indexes("exams")}
    with op.batch_alter_table("exams") as batch:
        batch.alter_column("slug", existing_type=sa.String(length=120), nullable=False)
        if "uq_exams_slug" not in constraints:
            batch.create_unique_constraint("uq_exams_slug", ["slug"])
    if "ix_exams_slug" not in indexes:
        op.create_index("ix_exams_slug", "exams", ["slug"])


def downgrade() -> None:
    indexes = {item.get("name") for item in sa.inspect(op.get_bind()).get_indexes("exams")}
    if "ix_exams_slug" in indexes:
        op.drop_index("ix_exams_slug", table_name="exams")
    with op.batch_alter_table("exams") as batch:
        batch.drop_constraint("uq_exams_slug", type_="unique")
        batch.drop_column("slug")
