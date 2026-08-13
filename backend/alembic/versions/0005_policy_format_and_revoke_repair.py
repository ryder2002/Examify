"""Store rich policy format and invalidate sessions tied to revoked tokens."""

from alembic import op
import sqlalchemy as sa


# alembic_version.version_num was created as VARCHAR(32) in the initial
# schema, so revision identifiers must remain within that durable limit.
revision = "0005_policy_revoke"
down_revision = "0004_desktop_sync"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _table_exists("site_policies"):
        op.create_table(
            "site_policies",
            sa.Column("key", sa.String(50), primary_key=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("content_format", sa.String(20), nullable=False, server_default="markdown"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    else:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("site_policies")}
        if "content_format" not in columns:
            op.add_column(
                "site_policies",
                sa.Column("content_format", sa.String(20), nullable=False, server_default="markdown"),
            )

    # Older releases marked a redeemed token as revoked without invalidating the
    # linked device.  Repair only those explicitly revoked token/device pairs.
    op.execute(
        sa.text(
            """
            UPDATE devices
            SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
            WHERE id IN (
              SELECT redeemed_by_device_id
              FROM activation_tokens
              WHERE status = 'revoked' AND redeemed_by_device_id IS NOT NULL
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE refresh_tokens
            SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
            WHERE device_id IN (
              SELECT redeemed_by_device_id
              FROM activation_tokens
              WHERE status = 'revoked' AND redeemed_by_device_id IS NOT NULL
            )
            """
        )
    )


def downgrade() -> None:
    if _table_exists("site_policies"):
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("site_policies")}
        if "content_format" in columns:
            op.drop_column("site_policies", "content_format")
