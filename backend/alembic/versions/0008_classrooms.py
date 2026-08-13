"""Add teacher roles, classrooms, assignments and classroom attempts."""

from alembic import op
import sqlalchemy as sa


revision = "0008_classrooms"
down_revision = "0007_exam_quota"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    token_columns = {
        column["name"] for column in inspector.get_columns("activation_tokens")
    }
    attempt_columns = {column["name"] for column in inspector.get_columns("attempts")}
    classroom_tables = {
        "classrooms",
        "class_members",
        "exam_versions",
        "exam_version_assets",
        "class_assignments",
        "anti_cheat_events",
    }
    required_attempt_columns = {
        "class_assignment_id",
        "class_member_id",
        "attempt_number",
        "answered_count",
        "current_question_number",
        "last_heartbeat_at",
        "is_fullscreen",
        "visibility_state",
        "deadline_at",
        "score_toeic",
        "listening_score",
        "reading_score",
        "time_spent_seconds",
        "submit_reason",
    }
    # Migration 0001 historically calls current Base.metadata.create_all().
    # A brand-new database can therefore already contain this revision's
    # schema before Alembic reaches 0008.
    if (
        "assigned_role" in token_columns
        and classroom_tables.issubset(tables)
        and required_attempt_columns.issubset(attempt_columns)
    ):
        return

    if "assigned_role" not in token_columns:
        op.add_column(
            "activation_tokens",
            sa.Column(
                "assigned_role",
                sa.String(length=20),
                nullable=False,
                server_default="user",
            ),
        )
        op.create_index(
            "ix_activation_tokens_assigned_role",
            "activation_tokens",
            ["assigned_role"],
        )

    op.create_table(
        "classrooms",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "owner_teacher_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("join_code", sa.String(length=8), nullable=False, unique=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_classrooms_owner_teacher_id", "classrooms", ["owner_teacher_id"])
    op.create_index("ix_classrooms_join_code", "classrooms", ["join_code"], unique=True)
    op.create_index("ix_classrooms_status", "classrooms", ["status"])

    op.create_table(
        "class_members",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "classroom_id",
            sa.String(length=36),
            sa.ForeignKey("classrooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("browser_key_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "classroom_id", "browser_key_hash", name="uq_class_member_browser"
        ),
    )
    op.create_index("ix_class_members_classroom_id", "class_members", ["classroom_id"])
    op.create_index("ix_class_members_browser_key_hash", "class_members", ["browser_key_hash"])
    op.create_index("ix_class_members_status", "class_members", ["status"])

    op.create_table(
        "exam_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "source_exam_id",
            sa.String(length=36),
            sa.ForeignKey("exams.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "owner_teacher_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("exam_type", sa.String(length=20), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("answer_key_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_exam_id", "version_number", name="uq_exam_version_number"
        ),
    )
    op.create_index("ix_exam_versions_source_exam_id", "exam_versions", ["source_exam_id"])
    op.create_index("ix_exam_versions_owner_teacher_id", "exam_versions", ["owner_teacher_id"])
    op.create_index("ix_exam_versions_exam_type", "exam_versions", ["exam_type"])
    op.create_index("ix_exam_versions_content_hash", "exam_versions", ["content_hash"])

    op.create_table(
        "exam_version_assets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "exam_version_id",
            sa.String(length=36),
            sa.ForeignKey("exam_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("bucket", sa.String(length=80), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=160), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64)),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_exam_version_assets_exam_version_id",
        "exam_version_assets",
        ["exam_version_id"],
    )
    op.create_index("ix_exam_version_assets_object_key", "exam_version_assets", ["object_key"])
    op.create_index("ix_exam_version_assets_sha256", "exam_version_assets", ["sha256"])

    op.create_table(
        "class_assignments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "classroom_id",
            sa.String(length=36),
            sa.ForeignKey("classrooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "exam_version_id",
            sa.String(length=36),
            sa.ForeignKey("exam_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="exam"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("opens_at", sa.DateTime(timezone=True)),
        sa.Column("closes_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("attempt_limit", sa.Integer()),
        sa.Column("score_release", sa.String(length=20), nullable=False, server_default="immediate"),
        sa.Column("answer_release", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column("anti_cheat_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("results_released_at", sa.DateTime(timezone=True)),
        sa.Column("answers_released_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_class_assignments_classroom_id", "class_assignments", ["classroom_id"])
    op.create_index("ix_class_assignments_exam_version_id", "class_assignments", ["exam_version_id"])
    op.create_index("ix_class_assignments_mode", "class_assignments", ["mode"])
    op.create_index("ix_class_assignments_status", "class_assignments", ["status"])

    for column in (
        sa.Column("class_assignment_id", sa.String(length=36), sa.ForeignKey("class_assignments.id", ondelete="CASCADE")),
        sa.Column("class_member_id", sa.String(length=36), sa.ForeignKey("class_members.id", ondelete="CASCADE")),
        sa.Column("attempt_number", sa.Integer()),
        sa.Column("answered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_question_number", sa.Integer()),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("is_fullscreen", sa.Boolean()),
        sa.Column("visibility_state", sa.String(length=20)),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("score_toeic", sa.Integer()),
        sa.Column("listening_score", sa.Integer()),
        sa.Column("reading_score", sa.Integer()),
        sa.Column("time_spent_seconds", sa.Integer()),
        sa.Column("submit_reason", sa.String(length=30)),
    ):
        op.add_column("attempts", column)
    op.create_index("ix_attempts_class_assignment_id", "attempts", ["class_assignment_id"])
    op.create_index("ix_attempts_class_member_id", "attempts", ["class_member_id"])
    op.create_index("ix_attempts_deadline_at", "attempts", ["deadline_at"])
    op.create_unique_constraint(
        "uq_class_attempt_number",
        "attempts",
        ["class_assignment_id", "class_member_id", "attempt_number"],
    )

    op.create_table(
        "anti_cheat_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.String(length=36),
            sa.ForeignKey("attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_event_id", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("client_occurred_at", sa.DateTime(timezone=True)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "attempt_id", "client_event_id", name="uq_attempt_client_event"
        ),
    )
    op.create_index("ix_anti_cheat_events_attempt_id", "anti_cheat_events", ["attempt_id"])
    op.create_index("ix_anti_cheat_events_event_type", "anti_cheat_events", ["event_type"])


def downgrade() -> None:
    op.drop_table("anti_cheat_events")
    op.drop_constraint("uq_class_attempt_number", "attempts", type_="unique")
    for index in (
        "ix_attempts_deadline_at",
        "ix_attempts_class_member_id",
        "ix_attempts_class_assignment_id",
    ):
        op.drop_index(index, table_name="attempts")
    for name in (
        "submit_reason",
        "time_spent_seconds",
        "reading_score",
        "listening_score",
        "score_toeic",
        "deadline_at",
        "visibility_state",
        "is_fullscreen",
        "last_heartbeat_at",
        "current_question_number",
        "answered_count",
        "attempt_number",
        "class_member_id",
        "class_assignment_id",
    ):
        op.drop_column("attempts", name)
    op.drop_table("class_assignments")
    op.drop_table("exam_version_assets")
    op.drop_table("exam_versions")
    op.drop_table("class_members")
    op.drop_table("classrooms")
    op.drop_index(
        "ix_activation_tokens_assigned_role", table_name="activation_tokens"
    )
    op.drop_column("activation_tokens", "assigned_role")
