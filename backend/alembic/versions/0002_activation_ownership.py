"""Keep exam ownership when an activation is reissued."""

from alembic import op
import sqlalchemy as sa


revision = "0002_activation_ownership"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _backfill_owner() -> None:
    op.execute(
        """
        UPDATE activation_tokens AS token
        SET owner_user_id = device.user_id
        FROM devices AS device
        WHERE token.redeemed_by_device_id = device.id
          AND token.owner_user_id IS NULL
        """
    )


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("activation_tokens")
    }
    # 0001 historically imports the current model metadata. On a brand-new
    # installation that can already include these columns, while an existing
    # 0001 database still needs the original migration below.
    if {"owner_user_id", "parent_token_id"}.issubset(columns):
        _backfill_owner()
        return
    op.add_column("activation_tokens", sa.Column("owner_user_id", sa.String(36), nullable=True))
    op.add_column("activation_tokens", sa.Column("parent_token_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_activation_tokens_owner_user",
        "activation_tokens",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_activation_tokens_parent",
        "activation_tokens",
        "activation_tokens",
        ["parent_token_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_activation_tokens_owner_user_id",
        "activation_tokens",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_activation_tokens_parent_token_id",
        "activation_tokens",
        ["parent_token_id"],
    )

    # Backfill existing redeemed tokens from their device so reissue works
    # immediately after this migration.
    _backfill_owner()


def downgrade() -> None:
    op.drop_index("ix_activation_tokens_parent_token_id", table_name="activation_tokens")
    op.drop_index("ix_activation_tokens_owner_user_id", table_name="activation_tokens")
    op.drop_constraint("fk_activation_tokens_parent", "activation_tokens", type_="foreignkey")
    op.drop_constraint("fk_activation_tokens_owner_user", "activation_tokens", type_="foreignkey")
    op.drop_column("activation_tokens", "parent_token_id")
    op.drop_column("activation_tokens", "owner_user_id")
