"""Add account onboarding, hardware identity and account classroom members."""

from alembic import op
import sqlalchemy as sa


revision = "0010_auth_student_publications"
down_revision = "0009_classroom_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "registered_at" not in user_columns:
        op.add_column("users", sa.Column("registered_at", sa.DateTime(timezone=True)))
        op.execute(
            "UPDATE users SET registered_at = created_at WHERE password_hash IS NOT NULL"
        )

    device_columns = {column["name"] for column in inspector.get_columns("devices")}
    if "identity_kind" not in device_columns:
        op.add_column(
            "devices",
            sa.Column(
                "identity_kind",
                sa.String(length=30),
                nullable=False,
                server_default="legacy_browser",
            ),
        )
        op.create_index("ix_devices_identity_kind", "devices", ["identity_kind"])
    if "hardware_key_hash" not in device_columns:
        op.add_column(
            "devices", sa.Column("hardware_key_hash", sa.String(length=64))
        )
        op.create_index(
            "ix_devices_hardware_key_hash",
            "devices",
            ["hardware_key_hash"],
            unique=False,
        )

    member_columns = {
        column["name"] for column in inspector.get_columns("class_members")
    }
    if "user_id" not in member_columns:
        op.add_column(
            "class_members",
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
            ),
        )
        op.create_index("ix_class_members_user_id", "class_members", ["user_id"])
        op.create_unique_constraint(
            "uq_class_member_user", "class_members", ["classroom_id", "user_id"]
        )
    # PostgreSQL and modern SQLite both permit multiple NULL values in this
    # legacy uniqueness constraint, so anonymous rows remain intact.
    with op.batch_alter_table("class_members") as batch:
        batch.alter_column(
            "browser_key_hash",
            existing_type=sa.String(length=64),
            nullable=True,
        )

    assignment_columns = {
        column["name"] for column in inspector.get_columns("class_assignments")
    }
    if "publication_kind" not in assignment_columns:
        op.add_column(
            "class_assignments", sa.Column("publication_kind", sa.String(length=30))
        )
        op.create_index(
            "ix_class_assignments_publication_kind",
            "class_assignments",
            ["publication_kind"],
        )
    if "publication_key" not in assignment_columns:
        op.add_column(
            "class_assignments", sa.Column("publication_key", sa.String(length=160))
        )
        op.create_index(
            "ix_class_assignments_publication_key",
            "class_assignments",
            ["publication_key"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("ix_class_assignments_publication_key", table_name="class_assignments")
    op.drop_index("ix_class_assignments_publication_kind", table_name="class_assignments")
    op.drop_column("class_assignments", "publication_key")
    op.drop_column("class_assignments", "publication_kind")
    op.drop_constraint("uq_class_member_user", "class_members", type_="unique")
    op.drop_index("ix_class_members_user_id", table_name="class_members")
    op.drop_column("class_members", "user_id")
    op.drop_index("ix_devices_hardware_key_hash", table_name="devices")
    op.drop_index("ix_devices_identity_kind", table_name="devices")
    op.drop_column("devices", "hardware_key_hash")
    op.drop_column("devices", "identity_kind")
    op.drop_column("users", "registered_at")
