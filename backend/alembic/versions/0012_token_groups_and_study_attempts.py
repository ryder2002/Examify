"""Add token folders, encrypted exports and per-attempt study configuration."""

from alembic import op
import sqlalchemy as sa


revision = "0012_token_groups_study"
down_revision = "0011_token_device_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "activation_token_groups" not in tables:
        op.create_table(
            "activation_token_groups",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("name_key", sa.String(length=160), nullable=False),
            sa.Column(
                "created_by_user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_activation_token_groups_name_key",
            "activation_token_groups",
            ["name_key"],
            unique=True,
        )
        op.create_index(
            "ix_activation_token_groups_created_by_user_id",
            "activation_token_groups",
            ["created_by_user_id"],
        )

    token_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("activation_tokens")
    }
    if "encrypted_code" not in token_columns:
        op.add_column("activation_tokens", sa.Column("encrypted_code", sa.Text()))
    if "group_id" not in token_columns:
        op.add_column(
            "activation_tokens",
            sa.Column(
                "group_id",
                sa.String(length=36),
                sa.ForeignKey("activation_token_groups.id", ondelete="SET NULL"),
            ),
        )
        op.create_index(
            "ix_activation_tokens_group_id", "activation_tokens", ["group_id"]
        )

    attempt_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("attempts")
    }
    if "launch_mode" not in attempt_columns:
        op.add_column("attempts", sa.Column("launch_mode", sa.String(length=30)))
        op.create_index("ix_attempts_launch_mode", "attempts", ["launch_mode"])
    if "selected_part_numbers" not in attempt_columns:
        op.add_column("attempts", sa.Column("selected_part_numbers", sa.JSON()))


def downgrade() -> None:
    op.drop_column("attempts", "selected_part_numbers")
    op.drop_index("ix_attempts_launch_mode", table_name="attempts")
    op.drop_column("attempts", "launch_mode")
    op.drop_index("ix_activation_tokens_group_id", table_name="activation_tokens")
    op.drop_column("activation_tokens", "group_id")
    op.drop_column("activation_tokens", "encrypted_code")
    op.drop_index(
        "ix_activation_token_groups_created_by_user_id",
        table_name="activation_token_groups",
    )
    op.drop_index(
        "ix_activation_token_groups_name_key",
        table_name="activation_token_groups",
    )
    op.drop_table("activation_token_groups")
