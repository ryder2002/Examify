"""Add editable guides, categories and MinIO-backed guide media."""

from alembic import op
import sqlalchemy as sa


revision = "0006_guides"
down_revision = "0005_policy_revoke"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    # `0001_initial` uses current Base.metadata for fresh installations. Keep
    # this revision safe both for those databases and for upgrades from 0005.
    if {"guide_categories", "guides", "guide_media"}.issubset(existing):
        return
    op.create_table(
        "guide_categories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("slug", sa.String(140), nullable=False, unique=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_guide_categories_slug", "guide_categories", ["slug"])
    op.create_index("ix_guide_categories_sort_order", "guide_categories", ["sort_order"])

    op.create_table(
        "guides",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(280), nullable=False, unique=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("thumbnail_url", sa.String(2048)),
        sa.Column("thumbnail_object_key", sa.String(1024)),
        sa.Column("category_id", sa.String(36), sa.ForeignKey("guide_categories.id", ondelete="SET NULL")),
        sa.Column("content", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("rendered_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_format", sa.String(20), nullable=False, server_default="tiptap-json"),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("keywords", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    for column in ("title", "slug", "thumbnail_object_key", "category_id", "status", "sort_order", "created_by"):
        op.create_index(f"ix_guides_{column}", "guides", [column])
    op.create_index("ix_guides_public_order", "guides", ["status", "sort_order", "updated_at"])

    op.create_table(
        "guide_media",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("original_name", sa.String(512), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("bucket", sa.String(80), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("mime_type", sa.String(160), nullable=False),
        sa.Column("media_type", sa.String(20), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("uploaded_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("object_key", "media_type", "uploaded_by"):
        op.create_index(f"ix_guide_media_{column}", "guide_media", [column])


def downgrade() -> None:
    op.drop_table("guide_media")
    op.drop_table("guides")
    op.drop_table("guide_categories")
