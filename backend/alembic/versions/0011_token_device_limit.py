"""Allow an activation token to authorize up to two devices."""

from alembic import op
import sqlalchemy as sa


revision = "0011_token_device_limit"
down_revision = "0010_auth_student_publications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    token_columns = {column["name"] for column in inspector.get_columns("activation_tokens")}
    if "max_devices" not in token_columns:
        op.add_column(
            "activation_tokens",
            sa.Column("max_devices", sa.Integer(), nullable=False, server_default="1"),
        )
        op.execute("UPDATE activation_tokens SET max_devices = 1 WHERE max_devices IS NULL")

    device_columns = {column["name"] for column in inspector.get_columns("devices")}
    if "activation_token_id" not in device_columns:
        op.add_column(
            "devices",
            sa.Column(
                "activation_token_id",
                sa.String(length=36),
                sa.ForeignKey("activation_tokens.id", ondelete="SET NULL"),
            ),
        )
        op.create_index(
            "ix_devices_activation_token_id",
            "devices",
            ["activation_token_id"],
        )
        # Preserve the existing one-device relationship for databases created
        # before multi-device activation was introduced.
        op.execute(
            "UPDATE devices SET activation_token_id = "
            "(SELECT id FROM activation_tokens "
            "WHERE activation_tokens.redeemed_by_device_id = devices.id "
            "ORDER BY activation_tokens.redeemed_at DESC NULLS LAST LIMIT 1) "
            "WHERE activation_token_id IS NULL"
        )


def downgrade() -> None:
    op.drop_index("ix_devices_activation_token_id", table_name="devices")
    op.drop_column("devices", "activation_token_id")
    op.drop_column("activation_tokens", "max_devices")
