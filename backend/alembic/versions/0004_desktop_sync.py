"""Add idempotent desktop synchronization records."""

from alembic import op
import sqlalchemy as sa


revision = "0004_desktop_sync"
down_revision = "0003_exam_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    exam_columns = {column["name"] for column in inspector.get_columns("exams")}
    if "client_exam_id" in exam_columns and "desktop_syncs" in inspector.get_table_names():
        return
    op.add_column("exams", sa.Column("client_exam_id", sa.String(36), nullable=True))
    op.create_index("ix_exams_client_exam_id", "exams", ["client_exam_id"])
    op.create_unique_constraint(
        "uq_exam_owner_client_id", "exams", ["owner_user_id", "client_exam_id"]
    )
    op.create_table(
        "desktop_syncs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_exam_id", sa.String(36), nullable=False),
        sa.Column(
            "exam_id",
            sa.String(36),
            sa.ForeignKey("exams.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="uploading"),
        sa.Column("manifest", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("uploaded_assets", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "client_exam_id", name="uq_sync_user_client"),
    )
    op.create_index("ix_desktop_syncs_user_id", "desktop_syncs", ["user_id"])
    op.create_index("ix_desktop_syncs_client_exam_id", "desktop_syncs", ["client_exam_id"])
    op.create_index("ix_desktop_syncs_exam_id", "desktop_syncs", ["exam_id"])
    op.create_index("ix_desktop_syncs_status", "desktop_syncs", ["status"])


def downgrade() -> None:
    op.drop_table("desktop_syncs")
    op.drop_constraint("uq_exam_owner_client_id", "exams", type_="unique")
    op.drop_index("ix_exams_client_exam_id", table_name="exams")
    op.drop_column("exams", "client_exam_id")
