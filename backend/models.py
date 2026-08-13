"""Relational model for durable exams, devices and OCR jobs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from exam_slug import default_exam_slug


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid4() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(160), default="User")
    password_hash: Mapped[str | None] = mapped_column(String(512))
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    exam_limit: Mapped[int | None] = mapped_column(Integer)
    exam_created_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ActivationTokenGroup(Base):
    __tablename__ = "activation_token_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(160))
    name_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ActivationToken(Base):
    __tablename__ = "activation_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_hint: Mapped[str] = mapped_column(String(12), index=True)
    encrypted_code: Mapped[str | None] = mapped_column(Text)
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("activation_token_groups.id", ondelete="SET NULL"), index=True
    )
    label: Mapped[str] = mapped_column(String(160), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)
    assigned_role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    exam_limit: Mapped[int] = mapped_column(Integer, default=5)
    max_devices: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redeemed_by_device_id: Mapped[str | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    parent_token_id: Mapped[str | None] = mapped_column(
        ForeignKey("activation_tokens.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    activation_token_id: Mapped[str | None] = mapped_column(
        ForeignKey("activation_tokens.id", ondelete="SET NULL"), index=True
    )
    device_key_hash: Mapped[str] = mapped_column(String(64), index=True)
    identity_kind: Mapped[str] = mapped_column(
        String(30), default="legacy_browser", index=True
    )
    hardware_key_hash: Mapped[str | None] = mapped_column(
        String(64), index=True
    )
    name: Mapped[str] = mapped_column(String(160), default="Browser")
    platform: Mapped[str] = mapped_column(String(80), default="")
    app_version: Mapped[str] = mapped_column(String(40), default="")
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "device_key_hash", name="uq_user_device_key"),
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    exam_type: Mapped[str] = mapped_column(String(20), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    pipeline_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(255), default="")
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    source_object_key: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_jobs_cache", "file_hash", "exam_type", "pipeline_version", "status"),
    )


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(
        String(120), unique=True, index=True, default=default_exam_slug
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    client_exam_id: Mapped[str | None] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(255))
    library_scope: Mapped[str] = mapped_column(
        String(24), default="personal", index=True
    )
    content_revision: Mapped[int] = mapped_column(Integer, default=1)
    current_version_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "exam_versions.id",
            ondelete="SET NULL",
            name="fk_exams_current_version_id",
            use_alter=True,
        ),
        index=True,
    )
    shared_title_key: Mapped[str | None] = mapped_column(
        String(1024), unique=True, index=True
    )
    last_edited_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    category: Mapped[str] = mapped_column(String(120), default="", index=True)
    exam_type: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), default="ready", index=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=2)
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    answer_key_count: Mapped[int] = mapped_column(Integer, default=0)
    solution_entry_count: Mapped[int] = mapped_column(Integer, default=0)
    solution_question_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=75)
    source_hash: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "client_exam_id", name="uq_exam_owner_client_id"
        ),
    )


class DesktopSync(Base):
    __tablename__ = "desktop_syncs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    client_exam_id: Mapped[str] = mapped_column(String(36), index=True)
    exam_id: Mapped[str | None] = mapped_column(
        ForeignKey("exams.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="uploading", index=True)
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    uploaded_assets: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("user_id", "client_exam_id", name="uq_sync_user_client"),
    )


class QuestionRecord(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    exam_id: Mapped[str] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    part: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    option_letters: Mapped[list] = mapped_column(JSON, default=list)
    correct: Mapped[str | None] = mapped_column(String(1))
    group_id: Mapped[str | None] = mapped_column(String(160), index=True)
    stimulus_id: Mapped[str | None] = mapped_column(String(160), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=100)
    issues: Mapped[list] = mapped_column(JSON, default=list)

    __table_args__ = (UniqueConstraint("exam_id", "number", name="uq_exam_question"),)


class StimulusRecord(Base):
    __tablename__ = "stimuli"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    exam_id: Mapped[str] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(30), default="image")
    question_numbers: Mapped[list] = mapped_column(JSON, default=list)
    page_numbers: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=100)
    issues: Mapped[list] = mapped_column(JSON, default=list)

    __table_args__ = (UniqueConstraint("exam_id", "source_id", name="uq_exam_stimulus"),)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    exam_id: Mapped[str | None] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True
    )
    stimulus_id: Mapped[str | None] = mapped_column(
        ForeignKey("stimuli.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    bucket: Mapped[str] = mapped_column(String(80))
    object_key: Mapped[str] = mapped_column(String(1024), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(160))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    page_number: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list | None] = mapped_column(JSON)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnswerKey(Base):
    __tablename__ = "answer_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    exam_id: Mapped[str] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True
    )
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    source_object_key: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Classroom(Base):
    __tablename__ = "classrooms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    owner_teacher_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    join_code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ClassroomCoTeacher(Base):
    __tablename__ = "classroom_co_teachers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    classroom_id: Mapped[str] = mapped_column(
        ForeignKey("classrooms.id", ondelete="CASCADE"), index=True
    )
    teacher_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    invited_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("classroom_id", "teacher_user_id", name="uq_co_teacher"),
    )


class ClassMember(Base):
    __tablename__ = "class_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    classroom_id: Mapped[str] = mapped_column(
        ForeignKey("classrooms.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    full_name: Mapped[str] = mapped_column(String(160))
    browser_key_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "classroom_id", "browser_key_hash", name="uq_class_member_browser"
        ),
        UniqueConstraint("classroom_id", "user_id", name="uq_class_member_user"),
        Index(
            "ix_class_members_user_status_classroom",
            "user_id",
            "status",
            "classroom_id",
        ),
    )


class ExamVersion(Base):
    __tablename__ = "exam_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    source_exam_id: Mapped[str | None] = mapped_column(
        ForeignKey("exams.id", ondelete="SET NULL"), index=True
    )
    owner_teacher_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(255))
    exam_type: Mapped[str] = mapped_column(String(20), index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    question_count: Mapped[int] = mapped_column(Integer)
    answer_key_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "source_exam_id", "version_number", name="uq_exam_version_number"
        ),
        UniqueConstraint(
            "source_exam_id", "content_hash", name="uq_exam_version_content_hash"
        ),
    )


class ExamSource(Base):
    """Durable source PDFs used to reconstruct a copy-on-write edit draft."""

    __tablename__ = "exam_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    exam_id: Mapped[str] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True
    )
    component: Mapped[str] = mapped_column(String(20), default="main")
    bucket: Mapped[str] = mapped_column(String(80))
    object_key: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(160), default="application/pdf")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("exam_id", "component", name="uq_exam_source_component"),
    )


class ExamEditSession(Base):
    """A teacher-owned copy-on-write draft based on one Exam revision."""

    __tablename__ = "exam_edit_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    exam_id: Mapped[str] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True
    )
    editor_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    base_revision: Mapped[int] = mapped_column(Integer)
    job_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_exam_edit_sessions_exam_status", "exam_id", "status"),
        Index("ix_exam_edit_sessions_editor_status", "editor_user_id", "status"),
    )


class SolutionImport(Base):
    __tablename__ = "solution_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    exam_type: Mapped[str] = mapped_column(String(20), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(160), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    bucket: Mapped[str | None] = mapped_column(String(80))
    object_key: Mapped[str | None] = mapped_column(String(1024), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_solution_import_owner_created", "owner_user_id", "created_at"),
    )


class SystemState(Base):
    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ExamVersionQuestion(Base):
    """Small immutable scoring/validation projection for an exam snapshot."""

    __tablename__ = "exam_version_questions"

    exam_version_id: Mapped[str] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="CASCADE"), primary_key=True
    )
    question_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    part_number: Mapped[int | None] = mapped_column(Integer)
    correct: Mapped[str | None] = mapped_column(String(1))

    __table_args__ = (
        CheckConstraint(
            "part_number IS NULL OR part_number BETWEEN 1 AND 7",
            name="ck_exam_version_question_part",
        ),
        CheckConstraint(
            "correct IS NULL OR correct IN ('A', 'B', 'C', 'D')",
            name="ck_exam_version_question_correct",
        ),
    )


class ExamVersionAsset(Base):
    __tablename__ = "exam_version_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    exam_version_id: Mapped[str] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    bucket: Mapped[str] = mapped_column(String(80))
    object_key: Mapped[str] = mapped_column(String(1024), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(160))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ClassAssignment(Base):
    __tablename__ = "class_assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    classroom_id: Mapped[str] = mapped_column(
        ForeignKey("classrooms.id", ondelete="CASCADE"), index=True
    )
    exam_version_id: Mapped[str] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(20), default="exam", index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    attempt_limit: Mapped[int | None] = mapped_column(Integer)
    score_release: Mapped[str] = mapped_column(String(20), default="immediate")
    answer_release: Mapped[str] = mapped_column(String(20), default="manual")
    anti_cheat_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    listening_navigation_locked: Mapped[bool] = mapped_column(Boolean, default=True)
    publication_kind: Mapped[str | None] = mapped_column(String(30), index=True)
    publication_key: Mapped[str | None] = mapped_column(
        String(160), unique=True, index=True
    )
    results_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answers_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    exam_id: Mapped[str] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True
    )
    exam_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="RESTRICT"), index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    class_assignment_id: Mapped[str | None] = mapped_column(
        ForeignKey("class_assignments.id", ondelete="CASCADE"), index=True
    )
    class_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("class_members.id", ondelete="CASCADE"), index=True
    )
    attempt_number: Mapped[int | None] = mapped_column(Integer)
    launch_mode: Mapped[str | None] = mapped_column(String(30), index=True)
    selected_part_numbers: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="in_progress", index=True)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    time_left_seconds: Mapped[int] = mapped_column(Integer)
    correct_count: Mapped[int | None] = mapped_column(Integer)
    graded_count: Mapped[int] = mapped_column(Integer, default=0)
    answered_count: Mapped[int] = mapped_column(Integer, default=0)
    answer_revision: Mapped[int] = mapped_column(Integer, default=0)
    submit_receipt_id: Mapped[str | None] = mapped_column(String(36))
    submit_idempotency_key: Mapped[str | None] = mapped_column(String(80))
    submitted_answer_hash: Mapped[str | None] = mapped_column(String(64))
    current_question_number: Mapped[int | None] = mapped_column(Integer)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_fullscreen: Mapped[bool | None] = mapped_column(Boolean)
    visibility_state: Mapped[str | None] = mapped_column(String(20))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    score_toeic: Mapped[int | None] = mapped_column(Integer)
    listening_score: Mapped[int | None] = mapped_column(Integer)
    reading_score: Mapped[int | None] = mapped_column(Integer)
    time_spent_seconds: Mapped[int | None] = mapped_column(Integer)
    submit_reason: Mapped[str | None] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("submit_receipt_id", name="uq_attempt_submit_receipt"),
        UniqueConstraint(
            "class_assignment_id",
            "class_member_id",
            "attempt_number",
            name="uq_class_attempt_number",
        ),
        Index(
            "ix_attempts_user_status_submitted",
            "user_id",
            "status",
            "submitted_at",
        ),
        Index(
            "ix_attempts_user_exam_status_started",
            "user_id",
            "exam_id",
            "status",
            "started_at",
        ),
        Index(
            "ix_attempts_assignment_started",
            "class_assignment_id",
            "started_at",
        ),
        Index(
            "ix_attempts_assignment_member_started",
            "class_assignment_id",
            "class_member_id",
            "started_at",
        ),
        Index(
            "ix_attempts_in_progress_deadline",
            "deadline_at",
            postgresql_where=text(
                "status = 'in_progress' AND class_assignment_id IS NOT NULL"
            ),
            sqlite_where=text(
                "status = 'in_progress' AND class_assignment_id IS NOT NULL"
            ),
        ),
    )


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("attempts.id", ondelete="CASCADE")
    )
    question_number: Mapped[int] = mapped_column(Integer)
    selected: Mapped[str] = mapped_column(String(1))
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("attempt_id", "question_number", name="uq_attempt_answer"),
    )


class AttemptSyncBatch(Base):
    """Durable acknowledgement ledger for idempotent delta sync retries."""

    __tablename__ = "attempt_sync_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("attempts.id", ondelete="CASCADE")
    )
    batch_id: Mapped[str] = mapped_column(String(36))
    changes_hash: Mapped[str] = mapped_column(String(64))
    base_revision: Mapped[int] = mapped_column(Integer)
    accepted_revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    __table_args__ = (
        UniqueConstraint("attempt_id", "batch_id", name="uq_attempt_sync_batch"),
    )


class AntiCheatEvent(Base):
    __tablename__ = "anti_cheat_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("attempts.id", ondelete="CASCADE"), index=True
    )
    client_event_id: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    client_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "attempt_id", "client_event_id", name="uq_attempt_client_event"
        ),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SitePolicy(Base):
    __tablename__ = "site_policies"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)  # 'terms' or 'privacy'
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    content_format: Mapped[str] = mapped_column(String(20), default="markdown")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class GuideCategory(Base):
    __tablename__ = "guide_categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Guide(Base):
    __tablename__ = "guides"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(255), index=True)
    slug: Mapped[str] = mapped_column(String(280), unique=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048))
    thumbnail_object_key: Mapped[str | None] = mapped_column(String(1024), index=True)
    category_id: Mapped[str | None] = mapped_column(
        ForeignKey("guide_categories.id", ondelete="SET NULL"), index=True
    )
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    rendered_html: Mapped[str] = mapped_column(Text, default="")
    content_format: Mapped[str] = mapped_column(String(20), default="tiptap-json")
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    search_text: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_guides_public_order", "status", "sort_order", "updated_at"),
    )


class GuideMedia(Base):
    __tablename__ = "guide_media"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    file_name: Mapped[str] = mapped_column(String(512))
    original_name: Mapped[str] = mapped_column(String(512))
    object_key: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    bucket: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(String(2048))
    mime_type: Mapped[str] = mapped_column(String(160))
    media_type: Mapped[str] = mapped_column(String(20), index=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    uploaded_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExamTag(Base):
    __tablename__ = "exam_tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PublicExamShare(Base):
    __tablename__ = "public_exam_shares"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    exam_id: Mapped[str] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), unique=True, index=True
    )
    share_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PublicExamSubmission(Base):
    __tablename__ = "public_exam_submissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    share_id: Mapped[str] = mapped_column(
        ForeignKey("public_exam_shares.id", ondelete="CASCADE"), index=True
    )
    exam_id: Mapped[str] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), index=True
    )
    exam_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="RESTRICT"), index=True
    )
    student_name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(40), default="")
    email: Mapped[str | None] = mapped_column(String(320), default="")
    status: Mapped[str] = mapped_column(String(20), default="in_progress", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_spent_seconds: Mapped[int | None] = mapped_column(Integer, default=0)
    total_correct: Mapped[int | None] = mapped_column(Integer, default=0)
    question_count: Mapped[int | None] = mapped_column(Integer, default=0)
    score_toeic: Mapped[int | None] = mapped_column(Integer, default=0)
    listening_score: Mapped[int | None] = mapped_column(Integer, default=0)
    reading_score: Mapped[int | None] = mapped_column(Integer, default=0)
    part_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
