"""Durable exam, attempt, activation and admin API."""

from __future__ import annotations

import logging
import hashlib
import mimetypes
import re
import copy
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Literal
from urllib.parse import quote

import bleach
import jwt
import markdown
from bleach.css_sanitizer import CSSSanitizer
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from attempt_answers import (
    InvalidAttemptAnswer,
    normalize_attempt_answers,
    replace_attempt_answers,
)
from attempt_sync import (
    AttemptBatchReuse,
    AttemptRevisionConflict,
    AttemptSyncRequest,
    canonical_answers,
    sync_attempt_changes,
)
from auth_service import (
    clear_session_cookies,
    clear_onboarding_cookie,
    create_activation_code,
    current_identity,
    decode_onboarding,
    hash_password,
    identity_from_refresh,
    issue_onboarding_token,
    normalize_activation_code,
    require_admin,
    require_roles,
    set_onboarding_cookie,
    set_session_cookies,
    set_access_cookie,
    sha256,
    verify_password,
)
from config import settings
from database import session_scope
from identity_cache import identity_cache
from object_storage import storage
from exam_solutions import (
    SolutionValidationError,
    normalized_name_key,
    solution_coverage,
    validate_solutions,
)
from exam_bank_scope import teacher_scoped_title_key
from presence_store import presence_store
from full_test_components import abandon_pending_components
from models import (
    ActivationToken,
    ActivationTokenGroup,
    Asset,
    AnswerKey,
    AntiCheatEvent,
    Attempt,
    AttemptAnswer,
    AuditLog,
    Classroom,
    ClassAssignment,
    ClassMember,
    Device,
    DesktopSync,
    Exam,
    ExamEditSession,
    ExamSource,
    ExamVersion,
    ExamVersionQuestion,
    ExamVersionAsset,
    ExamTag,
    Job,
    QuestionRecord,
    RefreshToken,
    SitePolicy,
    SolutionImport,
    StimulusRecord,
    SystemState,
    User,
    utcnow,
    uuid4,
)
from token_exports import (
    TokenExportUnavailable,
    build_token_workbook,
    decrypt_activation_code,
    encrypt_activation_code,
)
from toeic_score import scores as toeic_scores


router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)
_AUTH_VERIFY_SLOTS = threading.BoundedSemaphore(
    max(1, settings.auth_verify_concurrency)
)


class ActivateRequest(BaseModel):
    code: str = Field(min_length=8, max_length=80)
    device_key: str = Field(min_length=16, max_length=512)
    device_name: str = Field(default="Browser", max_length=160)
    platform: str = Field(default="", max_length=80)
    app_version: str = Field(default="", max_length=40)
    client_kind: Literal["web", "desktop"] = "web"


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)
    device_key: str = Field(default="", max_length=512)
    device_name: str = "Admin browser"
    platform: str = ""
    client_kind: Literal["web", "desktop"] = "web"


class RegisterRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=160)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    password_confirmation: str = Field(min_length=8, max_length=128)
    setup_token: str | None = Field(default=None, max_length=2048)


class TokenCreateRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=1000)
    label: str = Field(default="", max_length=160)
    note: str = Field(default="", max_length=2000)
    assigned_role: str = Field(default="user")
    # Kept in the request for backward-compatible clients. Activation tokens
    # are permanent until an administrator revokes or deletes them.
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    exam_limit: int = Field(default=5, ge=1, le=10000)
    max_devices: int = Field(default=1, ge=1, le=2)
    group_id: str | None = None


class TokenGroupCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)


class TokenGroupUpdateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)


class TokenGroupMembershipRequest(BaseModel):
    token_ids: list[str] = Field(min_length=1, max_length=1000)
    group_id: str | None = None


class TokenBulkDeleteRequest(BaseModel):
    token_ids: list[str] = Field(min_length=1, max_length=1000)


class TokenReissueRequest(BaseModel):
    revoke_existing_devices: bool = True
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    note: str = Field(default="", max_length=2000)
    max_devices: int | None = Field(default=None, ge=1, le=2)


class UserTokenReissueRequest(TokenReissueRequest):
    label: str | None = Field(default=None, max_length=160)


class AttemptCreateRequest(BaseModel):
    duration_seconds: int | None = Field(default=None, ge=60, le=24 * 3600)


class AttemptAnswersRequest(BaseModel):
    answers: dict[str, str] = Field(max_length=200)
    time_left_seconds: int = Field(ge=0)
    client_revision: int | None = Field(default=None, ge=0)


class UserCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    role: str = Field(default="user")
    status: str = Field(default="active")
    exam_limit: int | None = Field(default=5, ge=1, le=10000)


class UserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    role: str | None = Field(default=None)
    status: str | None = Field(default=None)
    exam_limit: int | None = Field(default=None, ge=1, le=10000)
    device_limit: int | None = Field(default=None, ge=1, le=2)


class AdminPasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
    new_password_confirmation: str = Field(min_length=8, max_length=128)


class AdminUserPasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)
    new_password_confirmation: str = Field(min_length=8, max_length=128)


class PolicyUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    content_format: Literal["html", "markdown"] = "html"


class TagCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ExamUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, max_length=120)
    archived: bool | None = None


class CombineExamsRequest(BaseModel):
    listening_exam_id: str
    reading_exam_id: str
    title: str = Field(default="Full Test", min_length=1, max_length=255)
    category: str = Field(default="", max_length=120)


class FullTestEditFinalizeRequest(BaseModel):
    listening_job_id: str
    reading_job_id: str
    title: str = Field(min_length=1, max_length=255)
    category: str = Field(default="", max_length=120)


POLICY_KEYS = {"terms", "privacy"}
DEFAULT_POLICY_TITLES = {
    "terms": "Điều khoản dịch vụ",
    "privacy": "Chính sách bảo mật",
}
DEFAULT_POLICY_CONTENTS = {
    "terms": "## 1. Giới thiệu\nChào mừng bạn đến với **Examify**.\n\n## 2. Quy định sử dụng\nHệ thống hỗ trợ chuyển đổi đề thi TOEIC từ file PDF sang dạng bài thi tương tác.\n\n## 3. Bản quyền & Bảo mật\nToàn bộ dữ liệu của bạn được bảo mật tuyệt đối.",
    "privacy": "## 1. Thu thập dữ liệu\nExamify tôn trọng quyền riêng tư của người dùng.\n\n## 2. Lưu trữ dữ liệu\nẢnh và audio bài thi được lưu trữ an toàn trong MinIO storage riêng biệt.\n\n## 3. Cam kết\nKhông chia sẻ thông tin thiết bị và mã kích hoạt cho bên thứ ba.",
}
POLICY_CSS = CSSSanitizer(
    allowed_css_properties={"font-family", "font-size", "text-align"}
)
POLICY_TAGS = {
    "p", "br", "strong", "em", "u", "h1", "h2", "h3", "h4", "ul", "ol",
    "li", "blockquote", "a", "span",
}
POLICY_ATTRIBUTES = {
    "a": ["href", "target", "rel"],
    "span": ["style"],
    "p": ["style"],
    "h1": ["style"],
    "h2": ["style"],
    "h3": ["style"],
    "h4": ["style"],
    "li": ["style"],
}


def _render_policy_html(content: str, content_format: str) -> str:
    source = (
        content
        if content_format == "html"
        else markdown.markdown(content, extensions=["extra", "sane_lists"])
    )
    return bleach.clean(
        source,
        tags=POLICY_TAGS,
        attributes=POLICY_ATTRIBUTES,
        protocols={"http", "https", "mailto"},
        css_sanitizer=POLICY_CSS,
        strip=True,
    )


def _policy_payload(policy_key: str, policy: SitePolicy | None) -> dict[str, str]:
    title = policy.title if policy else DEFAULT_POLICY_TITLES[policy_key]
    content = policy.content if policy else DEFAULT_POLICY_CONTENTS[policy_key]
    content_format = getattr(policy, "content_format", "markdown") if policy else "markdown"
    return {
        "key": policy_key,
        "title": title,
        "content": content,
        "content_format": content_format,
        "rendered_html": _render_policy_html(content, content_format),
    }


def _revoke_device_sessions(session, device: Device | None, now: datetime) -> bool:
    if device is None:
        return False
    if device.revoked_at is None:
        device.revoked_at = now
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.device_id == device.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    identity_cache.invalidate_device(device.id)
    return True


def _duration_for(payload: dict[str, Any]) -> int:
    questions = payload.get("questions") or []
    listening = any(int(item.get("number", 0)) <= 100 for item in questions)
    reading = any(int(item.get("number", 0)) >= 101 for item in questions)
    return 120 if listening and reading else 45 if listening else 75


def _asset_job_id(default_job_id: str | None, value: Any) -> str | None:
    match = re.search(
        r"/api/extractions/([0-9a-fA-F-]{36})/(?:assets|audio)/",
        str(value or ""),
    )
    return match.group(1) if match else default_job_id


def persist_final_exam(
    payload: dict[str, Any],
    *,
    job_id: str | None,
    owner_user_id: str | None,
    title: str | None = None,
    category: str | None = None,
    quota_replacement_exam_ids: tuple[str, ...] = (),
    target_exam_id: str | None = None,
    client_exam_id: str | None = None,
    base_revision: int | None = None,
    defer_version_snapshot: bool = False,
    is_full_test_component: bool = False,
) -> str | None:
    """Persist a finalized payload and its normalized searchable rows."""
    if not owner_user_id:
        return None
    with session_scope() as session:
        actor = session.get(User, owner_user_id)
        if actor is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
        if target_exam_id:
            exam = session.scalar(
                select(Exam)
                .where(
                    Exam.id == target_exam_id,
                    Exam.deleted_at.is_(None),
                )
                .with_for_update()
            )
            if exam is None or not (
                exam.owner_user_id == owner_user_id or actor.role == "admin"
            ):
                raise HTTPException(status_code=404, detail="Không tìm thấy đề cần cập nhật")
            if base_revision is not None and exam.content_revision != base_revision:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "exam_revision_conflict",
                        "message": "Đề đã được Teacher khác cập nhật. Draft của bạn vẫn được giữ.",
                        "base_revision": base_revision,
                        "current_revision": exam.content_revision,
                    },
                )
        elif client_exam_id:
            exam = session.scalar(
                select(Exam)
                .where(
                    Exam.owner_user_id == owner_user_id,
                    Exam.client_exam_id == client_exam_id,
                    Exam.deleted_at.is_(None),
                )
                .with_for_update()
            )
            if (
                exam is not None
                and base_revision is not None
                and exam.content_revision != base_revision
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "exam_revision_conflict",
                        "message": "Đề đã được cập nhật; bản cũ không được ghi đè.",
                        "base_revision": base_revision,
                        "current_revision": exam.content_revision,
                    },
                )
        else:
            exam = (
                session.scalar(
                    select(Exam).where(
                        Exam.job_id == job_id,
                        Exam.owner_user_id == owner_user_id,
                        Exam.deleted_at.is_(None),
                    )
                )
                if job_id
                else None
            )
        questions = payload.get("questions") or []
        numbers = {int(item.get("number", 0)) for item in questions}
        is_full_test_listening_component = (
            title == "Listening Component"
            and payload.get("exam_type") == "listening"
            and numbers == set(range(1, 101))
        )
        is_pending_component = bool(
            is_full_test_component or is_full_test_listening_component
        )
        try:
            payload["solutions"] = validate_solutions(
                payload.get("solutions") or [],
                str(payload.get("exam_type") or "reading"),
            )
        except SolutionValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.issues) from exc
        audit_before = (
            {
                "revision": int(exam.content_revision or 1),
                "title": exam.title,
                "tag": exam.category,
                "exam_type": exam.exam_type,
                "question_count": int(exam.question_count or 0),
                "solution_question_count": int(exam.solution_question_count or 0),
                "payload_hash": hashlib.sha256(
                    json.dumps(
                        exam.payload or {},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            }
            if exam is not None
            else None
        )
        is_new_exam = exam is None
        if exam is None:
            user = session.scalar(
                select(User).where(User.id == owner_user_id).with_for_update()
            )
            if user is None:
                raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
            if (
                user.role != "admin"
                and user.exam_limit is not None
                and not is_full_test_listening_component
                and not quota_replacement_exam_ids
            ):
                if user.exam_created_count >= user.exam_limit:
                    logger.warning(
                        "EXAM_QUOTA_BLOCKED user_id=%s used=%s limit=%s exam_type=%s",
                        user.id,
                        user.exam_created_count,
                        user.exam_limit,
                        payload.get("exam_type"),
                    )
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            f"Đã đạt giới hạn {user.exam_limit} đề thi. "
                            "Vui lòng liên hệ quản trị viên để tăng hạn mức."
                        ),
                    )
                user.exam_created_count += 1
                logger.info(
                    "EXAM_QUOTA_CONSUMED user_id=%s used=%s limit=%s exam_type=%s",
                    user.id,
                    user.exam_created_count,
                    user.exam_limit,
                    payload.get("exam_type"),
                )
            exam_id = str(uuid.uuid4())
            resolved_title = title or f"{payload.get('exam_type', 'exam').title()} Exam"
            from exam_slug import build_exam_slug

            exam = Exam(
                id=exam_id,
                slug=build_exam_slug(resolved_title, exam_id),
                owner_user_id=owner_user_id,
                job_id=job_id,
                title=resolved_title,
                exam_type=str(payload.get("exam_type", "reading")),
                library_scope=(
                    "teacher_shared"
                    if user.role in {"teacher", "admin"}
                    and not is_full_test_component
                    and not is_full_test_listening_component
                    else "personal"
                ),
                content_revision=1,
                last_edited_by_user_id=owner_user_id,
            )
            session.add(exam)
            session.flush()
        else:
            session.execute(delete(Asset).where(Asset.exam_id == exam.id))
            session.execute(delete(QuestionRecord).where(QuestionRecord.exam_id == exam.id))
            session.execute(delete(StimulusRecord).where(StimulusRecord.exam_id == exam.id))
            session.execute(delete(AnswerKey).where(AnswerKey.exam_id == exam.id))
        if is_full_test_component:
            # Idempotent retries of an intermediate finalize must not expose a
            # half exam in the Teacher shared bank.
            exam.library_scope = "personal"
            exam.shared_title_key = None
        exam.payload = payload
        if client_exam_id:
            exam.client_exam_id = client_exam_id
        exam.title = title or exam.title
        if exam.library_scope == "teacher_shared":
            # A title is unique only inside one Teacher + Tag catalogue. This
            # permits ``TEST 1`` under 2018 and 2019 while preserving the
            # duplicate guard for the same tag.
            category_for_key = (
                " ".join(
                    (category if category is not None else (exam.category or "")).split()
                )
            )
            title_key = teacher_scoped_title_key(
                owner_user_id, exam.title, category_for_key
            )
            if not title_key:
                raise HTTPException(status_code=422, detail="Tên đề không được để trống")
            duplicate = session.scalar(
                select(Exam.id).where(
                    Exam.shared_title_key == title_key,
                    Exam.id != exam.id,
                    Exam.deleted_at.is_(None),
                )
            )
            if duplicate:
                if duplicate in quota_replacement_exam_ids:
                    # Backward compatibility for a browser that finalized its
                    # Reading component as shared before calling combine. Free
                    # the normalized title in this same transaction so the
                    # single replacement Full Test can claim it atomically.
                    replacement = session.get(Exam, duplicate)
                    if replacement is not None:
                        replacement.shared_title_key = None
                        session.flush()
                else:
                    raise HTTPException(
                        status_code=409,
                        detail="Tên đề đã tồn tại trong Kho đề thi chung.",
                    )
            exam.shared_title_key = title_key
        else:
            exam.shared_title_key = None
        if category is not None:
            category_name = " ".join(category.split())
            if exam.library_scope == "teacher_shared" and category_name:
                category_key = normalized_name_key(category_name)
                tag = session.scalar(
                    select(ExamTag).where(ExamTag.name_key == category_key)
                )
                if tag is None:
                    tag = ExamTag(name=category_name, name_key=category_key)
                    session.add(tag)
                    try:
                        session.flush()
                    except IntegrityError as exc:
                        raise HTTPException(
                            status_code=409,
                            detail="Tag vừa được Teacher khác tạo; hãy tải lại Kho Tag.",
                        ) from exc
                exam.category = tag.name
            else:
                exam.category = category_name
        exam.exam_type = str(payload.get("exam_type", exam.exam_type))
        exam.question_count = len(questions)
        exam.answer_key_count = sum(bool(item.get("correct")) for item in questions)
        solution_entries, solution_questions = solution_coverage(payload["solutions"])
        exam.solution_entry_count = solution_entries
        exam.solution_question_count = solution_questions
        exam.duration_minutes = _duration_for(payload)
        # Intermediate Full Test components are durable so combine/retry can
        # use them, but they are not standalone exams and must never appear in
        # My Exams. The combined 200-question record is the only published
        # exam; a failed browser redirect therefore cannot expose duplicates.
        exam.status = "component_pending" if is_pending_component else "ready"
        exam.last_edited_by_user_id = owner_user_id
        if not is_new_exam:
            exam.content_revision = max(1, exam.content_revision or 1) + 1
        exam.updated_at = utcnow()
        for item in questions:
            session.add(
                QuestionRecord(
                    exam_id=exam.id,
                    number=int(item["number"]),
                    part=str(item.get("part", "")),
                    text=str(item.get("text", "")),
                    options=item.get("options") or {},
                    option_letters=item.get("option_letters") or [],
                    correct=item.get("correct"),
                    group_id=item.get("group_id"),
                    stimulus_id=item.get("stimulus_id"),
                    confidence=float(item.get("confidence", 100)),
                    issues=item.get("issues") or [],
                )
            )
        for item in payload.get("stimuli") or []:
            stimulus_row = StimulusRecord(
                    exam_id=exam.id,
                    source_id=str(item["id"]),
                    title=str(item.get("title", "")),
                    kind=str(item.get("kind", "image")),
                    question_numbers=item.get("question_numbers") or [],
                    page_numbers=item.get("page_numbers") or [],
                    confidence=float(item.get("confidence", 100)),
                    issues=item.get("issues") or [],
                )
            session.add(stimulus_row)
            session.flush()
            for order, asset in enumerate(item.get("assets") or []):
                asset_id = str(asset.get("id", ""))
                asset_job_id = _asset_job_id(job_id, asset.get("url"))
                session.add(
                    Asset(
                        exam_id=exam.id,
                        stimulus_id=stimulus_row.id,
                        kind="stimulus",
                        bucket="examify-assets",
                        object_key=(
                            f"jobs/{asset_job_id}/assets/{asset_id}"
                            if asset_job_id
                            else asset.get("url", "")
                        ),
                        filename=asset_id,
                        content_type="image/webp",
                        size=0,
                        page_number=asset.get("page"),
                        bbox=asset.get("bbox"),
                        display_order=order,
                    )
                )
        audios_list = list(payload.get("audios") or [])
        if payload.get("audio") and not any(a.get("id") == payload["audio"].get("id") for a in audios_list):
            audios_list.append(payload["audio"])
        for order, audio in enumerate(audios_list):
            audio_id = str(audio.get("id", ""))
            audio_job_id = _asset_job_id(job_id, audio.get("url"))
            session.add(
                Asset(
                    exam_id=exam.id,
                    kind="audio",
                    bucket="examify-audio",
                    object_key=(
                        f"jobs/{audio_job_id}/audio/{audio_id}"
                        if audio_job_id
                        else audio.get("url", "")
                    ),
                    filename=audio_id or str(audio.get("filename") or ""),
                    content_type=str(audio.get("content_type") or "audio/mpeg"),
                    size=int(audio.get("size") or 0),
                    display_order=order,
                )
            )
        answers = {
            str(item["number"]): item["correct"]
            for item in questions
            if item.get("correct")
        }
        session.add(AnswerKey(exam_id=exam.id, answers=answers, source="finalize"))
        try:
            session.flush()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="Tên đề đã tồn tại trong Kho đề thi chung.",
            ) from exc
        if not defer_version_snapshot:
            # Import lazily to avoid the platform/classroom router import cycle.
            from classroom_api import _snapshot_exam

            version = _snapshot_exam(session, exam, owner_user_id)
            exam.current_version_id = version.id
        if storage is not None:
            component_jobs = dict(payload.get("component_job_ids") or {})
            if not component_jobs and job_id and re.fullmatch(
                r"[0-9a-fA-F-]{36}", job_id
            ):
                component_jobs = {
                    str(payload.get("exam_type") or "main"): job_id
                }
            for component, source_job_id in component_jobs.items():
                if not re.fullmatch(r"[0-9a-fA-F-]{36}", str(source_job_id)):
                    continue
                destination = f"examify-sources/{exam.id}/{component}.pdf"
                source_key = f"jobs/{source_job_id}/input.pdf"
                try:
                    storage.copy_object(
                        settings.minio_bucket_sources, source_key, destination
                    )
                    source_stat = storage.client.stat_object(
                        settings.minio_bucket_sources, destination
                    )
                except Exception:
                    logger.warning(
                        "EXAM_SOURCE_COPY_FAILED exam_id=%s component=%s source_job=%s",
                        exam.id,
                        component,
                        source_job_id,
                        exc_info=True,
                    )
                    continue
                source = session.scalar(
                    select(ExamSource).where(
                        ExamSource.exam_id == exam.id,
                        ExamSource.component == component,
                    )
                )
                if source is None:
                    source = ExamSource(
                        exam_id=exam.id,
                        component=component,
                        bucket=settings.minio_bucket_sources,
                        object_key=destination,
                        filename=f"{component}.pdf",
                    )
                    session.add(source)
                source.object_key = destination
                source.size = int(source_stat.size or 0)
        if exam.library_scope == "teacher_shared":
            payload_hash = hashlib.sha256(
                json.dumps(
                    exam.payload or {},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            changed_fields = [
                field
                for field, old_value, new_value in (
                    ("title", audit_before and audit_before["title"], exam.title),
                    ("tag", audit_before and audit_before["tag"], exam.category),
                    ("exam_type", audit_before and audit_before["exam_type"], exam.exam_type),
                    (
                        "question_count",
                        audit_before and audit_before["question_count"],
                        exam.question_count,
                    ),
                    (
                        "solution_question_count",
                        audit_before and audit_before["solution_question_count"],
                        exam.solution_question_count,
                    ),
                    (
                        "content",
                        audit_before and audit_before["payload_hash"],
                        payload_hash,
                    ),
                )
                if old_value != new_value
            ]
            session.add(
                AuditLog(
                    actor_user_id=owner_user_id,
                    action=(
                        "exam_bank.created" if is_new_exam else "exam_bank.finalized"
                    ),
                    target_type="exam",
                    target_id=exam.id,
                    detail={
                        "previous_revision": (
                            audit_before["revision"] if audit_before else 0
                        ),
                        "new_revision": exam.content_revision,
                        "changed_fields": changed_fields,
                    },
                )
            )
        session.flush()
        return exam.id


def _asset_access_token(exam_id: str, asset_id: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "type": "exam_asset",
            "exam_id": exam_id,
            "asset_id": asset_id,
            "iat": now,
            "exp": now + timedelta(hours=2),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def _valid_asset_access_token(token: str, exam_id: str, asset_id: str) -> bool:
    if not token:
        return False
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    return bool(
        payload.get("type") == "exam_asset"
        and payload.get("exam_id") == exam_id
        and payload.get("asset_id") == asset_id
    )


def _exam_payload(exam: Exam, session: Any = None) -> dict[str, Any]:
    payload = dict(exam.payload or {})
    payload["exam_id"] = exam.id
    payload["slug"] = exam.slug
    payload["title"] = exam.title
    payload["category"] = exam.category
    if session is not None and storage is not None:
        assets = session.scalars(
            select(Asset).where(Asset.exam_id == exam.id)
        ).all()
        if assets:
            urls = {}
            for asset in assets:
                try:
                    if settings.minio_public_url:
                        presigned = storage.client.presigned_get_object(
                            asset.bucket, storage.safe_key(asset.object_key)
                        )
                        parsed_endpoint = f"{'https' if settings.minio_secure else 'http'}://{settings.minio_endpoint}"
                        if presigned.startswith(parsed_endpoint):
                            presigned = settings.minio_public_url + presigned[len(parsed_endpoint):]
                        elif "://" in presigned:
                            parts = presigned.split("/", 3)
                            if len(parts) >= 4:
                                presigned = f"{settings.minio_public_url}/{parts[3]}"
                        if asset.id:
                            urls[asset.id] = presigned
                        if asset.filename:
                            urls[asset.filename] = presigned
                    else:
                        asset_key = asset.filename or asset.id
                        if asset_key:
                            version = (
                                int(asset.updated_at.timestamp())
                                if getattr(asset, "updated_at", None)
                                else "2"
                            )
                            access_token = _asset_access_token(exam.id, asset.id)
                            ref_url = (
                                f"/api/v1/exams/{exam.id}/assets/{quote(asset_key, safe='')}"
                                f"?v={version}&token={quote(access_token, safe='')}"
                            )
                            if asset.id:
                                urls[asset.id] = ref_url
                            if asset.filename:
                                urls[asset.filename] = ref_url
                except Exception:
                    pass
            for stimulus in payload.get("stimuli") or []:
                for asset in stimulus.get("assets") or []:
                    target_url = urls.get(asset.get("id")) or urls.get(asset.get("filename"))
                    if target_url:
                        asset["url"] = target_url
            for audio in payload.get("audios") or []:
                target_url = urls.get(audio.get("id")) or urls.get(audio.get("filename"))
                if target_url:
                    audio["url"] = target_url
            if payload.get("audio"):
                target_url = urls.get(payload["audio"].get("id")) or urls.get(payload["audio"].get("filename"))
                if target_url:
                    payload["audio"]["url"] = target_url
    return payload


def _public_exam_payload(exam: Exam, session: Any = None) -> dict[str, Any]:
    """Return public exam content without answer keys."""

    payload = copy.deepcopy(_exam_payload(exam, session))
    payload.pop("answer_key", None)
    payload.pop("solutions", None)
    for question in payload.get("questions") or []:
        if isinstance(question, dict):
            question.pop("correct", None)
    return payload


def _public_version_payload(
    session: Any, exam: Exam, version: ExamVersion | None
) -> dict[str, Any]:
    if version is None:
        return _public_exam_payload(exam, session)
    from classroom_api import _exam_for_student

    payload = copy.deepcopy(_exam_for_student(session, version, reveal_answers=False))
    # Anonymous public tests use the strictest shape: omit the key entirely so
    # no client can confuse a hidden answer with an answerless question.
    for question in payload.get("questions") or []:
        if isinstance(question, dict):
            question.pop("correct", None)
    return payload


def _attempt_version(session: Any, attempt: Attempt) -> ExamVersion | None:
    return session.get(ExamVersion, attempt.exam_version_id) if attempt.exam_version_id else None


def _attempt_exam_payload(
    session: Any,
    attempt: Attempt,
    exam: Exam | None,
    *,
    reveal_answers: bool,
) -> dict[str, Any]:
    version = _attempt_version(session, attempt)
    if version is None:
        if exam is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy đề thi")
        payload = copy.deepcopy(_exam_payload(exam, session))
        if not reveal_answers:
            payload.pop("answer_key", None)
            for question in payload.get("questions") or []:
                question.pop("correct", None)
    else:
        from classroom_api import _exam_for_student

        payload = copy.deepcopy(
            _exam_for_student(
                session,
                version,
                reveal_answers=reveal_answers,
                attempt=attempt,
            )
        )
    # Solutions are deliberately lazy-loaded through the owner-only endpoint.
    payload.pop("solutions", None)
    return payload


def _attempt_question_rows(
    session: Any, attempt: Attempt
) -> list[tuple[int, int | None, str | None]]:
    if attempt.exam_version_id:
        rows = session.execute(
            select(
                ExamVersionQuestion.question_number,
                ExamVersionQuestion.part_number,
                ExamVersionQuestion.correct,
            ).where(ExamVersionQuestion.exam_version_id == attempt.exam_version_id)
        ).all()
        if attempt.selected_part_numbers:
            selected = {int(part) for part in attempt.selected_part_numbers}
            rows = [row for row in rows if row.part_number in selected]
        return [(row.question_number, row.part_number, row.correct) for row in rows]
    return [
        (row.number, None, row.correct)
        for row in session.scalars(
            select(QuestionRecord).where(QuestionRecord.exam_id == attempt.exam_id)
        ).all()
    ]


def _can_manage_exam(exam: Exam, identity: dict[str, Any]) -> bool:
    role = str(identity.get("role") or "").casefold()
    return (
        role == "admin"
        or (
            role == "teacher" and exam.library_scope == "teacher_shared"
        )
        or exam.owner_user_id == identity.get("user_id")
    )


def _public_submission_token(submission_id: str, share_id: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": submission_id,
            "share_id": share_id,
            "type": "public_submission",
            "iat": now,
            "exp": now + timedelta(hours=12),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def _verify_public_submission_token(token: str, submission_id: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Phiên public test không hợp lệ") from exc
    if payload.get("type") != "public_submission" or payload.get("sub") != submission_id:
        raise HTTPException(status_code=401, detail="Phiên public test không hợp lệ")
    return payload


@router.api_route("/exams/{exam_id}/assets/{asset_id}", methods=["GET", "HEAD"])
def get_exam_public_asset(exam_id: str, asset_id: str, request: Request):
    identity = current_identity(request, required=False)
    access_token = request.query_params.get("token", "")
    from models import PublicExamShare
    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        if not exam or exam.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài thi")
        public_share_active = session.scalar(
            select(PublicExamShare.id).where(
                PublicExamShare.exam_id == exam.id,
                PublicExamShare.is_active == True,
            )
        ) is not None
        if not public_share_active and not (
            identity
            and (
                str(identity.get("role") or "").casefold() == "admin"
                or identity.get("user_id") == exam.owner_user_id
            )
        ) and not access_token:
            raise HTTPException(status_code=403, detail="Không có quyền truy cập asset này")
        asset = session.scalar(
            select(Asset).where(Asset.exam_id == exam_id, Asset.filename == asset_id)
        )
        if asset is None:
            payload_assets = [
                asset_ref
                for stimulus in (exam.payload or {}).get("stimuli") or []
                for asset_ref in stimulus.get("assets") or []
            ] + list((exam.payload or {}).get("audios") or [])
            matching = next(
                (
                    item
                    for item in payload_assets
                    if str(item.get("id")) == asset_id or str(item.get("filename")) == asset_id
                ),
                None,
            )
            if matching:
                asset = session.scalar(
                    select(Asset).where(
                        Asset.exam_id == exam_id,
                        Asset.filename == str(matching.get("filename") or asset_id),
                    )
                )
        if not asset:
            raise HTTPException(status_code=404, detail="Không tìm thấy asset")
        if not public_share_active and not (
            identity
            and (
                str(identity.get("role") or "").casefold() == "admin"
                or identity.get("user_id") == exam.owner_user_id
            )
        ) and not _valid_asset_access_token(access_token, exam.id, asset.id):
            raise HTTPException(status_code=403, detail="Token asset không hợp lệ")
        bucket, object_key, content_type = (
            asset.bucket,
            asset.object_key,
            asset.content_type,
        )
        if not content_type or content_type == "application/octet-stream":
            guessed = (
                mimetypes.guess_type(asset_id)[0]
                or mimetypes.guess_type(asset.filename or "")[0]
            )
            if guessed:
                content_type = guessed

    if storage is None:
        raise HTTPException(status_code=503, detail="Object storage chưa sẵn sàng")

    if settings.minio_accel_redirect_prefix:
        internal_path = storage.presigned_internal_redirect(
            bucket,
            object_key,
            settings.minio_accel_redirect_prefix,
            method=request.method,
        )
        return Response(
            media_type=content_type,
            headers={
                "X-Accel-Redirect": internal_path,
                "X-Accel-Expires": "3600",
                "Cache-Control": "private, max-age=3600",
                "Accept-Ranges": "bytes",
            },
        )

    response = storage.client.get_object(bucket, storage.safe_key(object_key))

    def body():
        try:
            yield from response.stream(1024 * 1024)
        finally:
            response.close()
            response.release_conn()

    return StreamingResponse(body(), media_type=content_type)


@router.post("/activations/redeem")
def redeem_activation(
    body: ActivateRequest, request: Request, response: Response
) -> dict[str, Any]:
    token_hash = sha256(normalize_activation_code(body.code))
    key_hash = sha256(body.device_key)
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        token = session.scalar(
            select(ActivationToken)
            .where(ActivationToken.token_hash == token_hash)
            .with_for_update()
        )
        if token is None:
            raise HTTPException(status_code=404, detail="Mã kích hoạt không hợp lệ")
        if token.status in {"revoked", "expired"}:
            raise HTTPException(status_code=409, detail="Mã kích hoạt đã bị thu hồi hoặc hết hạn")
        expires_at = token.expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at and expires_at < now:
            token.status = "expired"
            raise HTTPException(status_code=410, detail="Mã kích hoạt đã hết hạn")
        user = session.get(User, token.owner_user_id) if token.owner_user_id else None
        bound_device = (
            session.get(Device, token.redeemed_by_device_id)
            if token.redeemed_by_device_id
            else None
        )
        if token.status == "redeemed" or token.redeemed_at is not None:
            if user is None:
                raise HTTPException(status_code=409, detail="Chủ sở hữu mã không còn tồn tại")
            linked_devices = session.scalars(
                select(Device).where(
                    Device.user_id == user.id,
                    Device.activation_token_id == token.id,
                    Device.revoked_at.is_(None),
                )
            ).all()
            # Legacy databases may have only the old primary device pointer.
            if (
                bound_device
                and bound_device.revoked_at is None
                and bound_device not in linked_devices
            ):
                linked_devices.append(bound_device)
            device = next(
                (
                    item
                    for item in linked_devices
                    if key_hash
                    in {item.device_key_hash, item.hardware_key_hash}
                ),
                None,
            )
            if device is None:
                if len(linked_devices) >= max(1, min(2, token.max_devices or 1)):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Mã kích hoạt đã đạt giới hạn thiết bị. "
                            "Vui lòng dùng thiết bị đã kích hoạt hoặc liên hệ Admin."
                        ),
                    )
                device = session.scalar(
                    select(Device).where(
                        Device.user_id == user.id,
                        or_(
                            Device.device_key_hash == key_hash,
                            Device.hardware_key_hash == key_hash,
                        ),
                    )
                )
                if device is not None and device.revoked_at is None:
                    raise HTTPException(
                        status_code=409,
                        detail="Thiết bị này đang được gắn với một Key khác",
                    )
                if device is None:
                    device = Device(user_id=user.id, device_key_hash=key_hash)
                    session.add(device)
                device.device_key_hash = key_hash
                device.hardware_key_hash = (
                    key_hash if body.client_kind == "desktop" else None
                )
                device.activation_token_id = token.id
                device.identity_kind = (
                    "desktop_hardware" if body.client_kind == "desktop" else "web_activation"
                )
                device.name = body.device_name
                device.platform = body.platform
                device.app_version = body.app_version
                device.revoked_at = None
                device.last_seen_at = now
                session.flush()
                identity_cache.invalidate_device(device.id)
            if user.registered_at and user.password_hash:
                clear_onboarding_cookie(response)
                return {
                    "user_id": user.id,
                    "device_id": device.id,
                    "role": user.role,
                    "next": "login",
                    "registration_required": False,
                }
            setup = issue_onboarding_token(user.id, device.id, token.id)
            set_onboarding_cookie(response, setup, request=request)
            return {
                "user_id": user.id,
                "device_id": device.id,
                "role": user.role,
                "next": "register",
                "registration_required": True,
                "setup_token": setup if body.client_kind == "desktop" else None,
            }
        if token.status != "available":
            raise HTTPException(status_code=409, detail="Mã kích hoạt không khả dụng")
        if user is None:
            role = (
                token.assigned_role
                if token.assigned_role in {"user", "teacher", "student"}
                else "user"
            )
            user = User(
                display_name=token.label or "User",
                role=role,
                exam_limit=0 if role == "student" else token.exam_limit,
            )
            session.add(user)
            session.flush()
            token.owner_user_id = user.id
        elif user.exam_limit is None and user.role != "student":
            user.exam_limit = token.exam_limit
        if user.status != "active":
            raise HTTPException(status_code=403, detail="Tài khoản của mã đã bị khóa")
        device = session.scalar(
            select(Device).where(
                Device.user_id == user.id,
                or_(
                    Device.device_key_hash == key_hash,
                    Device.hardware_key_hash == key_hash,
                ),
            )
        )
        if device is None:
            device = Device(
                user_id=user.id,
                device_key_hash=key_hash,
                activation_token_id=token.id,
                hardware_key_hash=key_hash if body.client_kind == "desktop" else None,
                identity_kind=(
                    "desktop_hardware" if body.client_kind == "desktop" else "web_activation"
                ),
                name=body.device_name,
                platform=body.platform,
                app_version=body.app_version,
            )
            session.add(device)
            session.flush()
        else:
            # A newly issued one-time token explicitly authorizes this same
            # physical device again. Reuse the durable device row so reissue
            # cannot violate uq_user_device_key.
            device.name = body.device_name
            device.platform = body.platform
            device.app_version = body.app_version
            device.revoked_at = None
            device.last_seen_at = now
            device.activation_token_id = token.id
            if body.client_kind == "desktop":
                device.hardware_key_hash = key_hash
                device.identity_kind = "desktop_hardware"
            identity_cache.invalidate_device(device.id)
        token.status = "redeemed"
        token.redeemed_at = now
        token.redeemed_by_device_id = device.id
        session.add(
            AuditLog(
                actor_user_id=user.id,
                action="activation.redeemed",
                target_type="device",
                target_id=device.id,
                detail={"client_kind": body.client_kind},
            )
        )
        if user.registered_at and user.password_hash:
            clear_onboarding_cookie(response)
            return {
                "user_id": user.id,
                "device_id": device.id,
                "role": user.role,
                "next": "login",
                "registration_required": False,
            }
        setup = issue_onboarding_token(user.id, device.id, token.id)
        set_onboarding_cookie(response, setup, request=request)
        return {
            "user_id": user.id,
            "device_id": device.id,
            "role": user.role,
            "next": "register",
            "registration_required": True,
            "setup_token": setup if body.client_kind == "desktop" else None,
        }


@router.post("/auth/register")
def register(body: RegisterRequest, request: Request, response: Response) -> dict[str, Any]:
    if body.password != body.password_confirmation:
        raise HTTPException(status_code=422, detail="Xác nhận mật khẩu không khớp")
    raw_setup = body.setup_token or request.cookies.get("smart_exam_onboarding", "")
    if not raw_setup:
        raise HTTPException(status_code=401, detail="Bạn cần kích hoạt trước khi đăng ký")
    claims = decode_onboarding(raw_setup)
    email = body.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="Email không hợp lệ")
    with session_scope() as session:
        user = session.get(User, claims.get("sub"))
        device = session.get(Device, claims.get("device_id"))
        token = session.get(ActivationToken, claims.get("activation_token_id"))
        if (
            user is None
            or device is None
            or token is None
            or device.user_id != user.id
            or token.owner_user_id != user.id
            or device.activation_token_id != token.id
            or token.status != "redeemed"
            or token.redeemed_at is None
            or device.revoked_at is not None
        ):
            raise HTTPException(status_code=401, detail="Phiên đăng ký không còn hợp lệ")
        if user.registered_at or user.password_hash:
            raise HTTPException(status_code=409, detail="Tài khoản đã được đăng ký")
        existing = session.scalar(
            select(User).where(User.email == email, User.id != user.id)
        )
        if existing:
            raise HTTPException(status_code=409, detail="Email đã được sử dụng")
        user.display_name = body.display_name.strip()
        if not token.label.strip():
            token.label = user.display_name
        user.email = email
        user.password_hash = hash_password(body.password)
        user.registered_at = utcnow()
        user.updated_at = utcnow()
        session.add(
            AuditLog(
                actor_user_id=user.id,
                action="auth.registered",
                target_type="user",
                target_id=user.id,
            )
        )
    clear_session_cookies(response)
    return {"ok": True, "next": "login", "email": email}


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    # Read only the fields needed for password verification, then release the
    # database connection while Argon2 performs its CPU-heavy work. Holding a
    # pool slot during verification was the direct cause of the 300-login
    # connection exhaustion benchmark.
    with session_scope() as session:
        auth_row = session.execute(
            select(
                User.id,
                User.password_hash,
                User.registered_at,
            ).where(User.email == body.email.lower())
        ).mappings().one_or_none()

    acquired = _AUTH_VERIFY_SLOTS.acquire(timeout=5)
    if not acquired:
        response.headers["Retry-After"] = "1"
        raise HTTPException(status_code=503, detail="Hệ thống đang xử lý đăng nhập")
    try:
        credentials_valid = bool(
            auth_row
            and auth_row["password_hash"]
            and auth_row["registered_at"] is not None
            and verify_password(auth_row["password_hash"], body.password)
        )
    finally:
        _AUTH_VERIFY_SLOTS.release()
    if not credentials_valid:
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng")

    user_id = str(auth_row["id"])
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng")
        if user.status != "active":
            raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa")
        key_hash = sha256(body.device_key) if body.device_key else ""
        if user.role == "admin":
            admin_key_hash = key_hash or sha256(
                f"admin:{user.id}:{body.device_name}"
            )
            device = session.scalar(
                select(Device).where(
                    Device.user_id == user.id,
                    Device.device_key_hash == admin_key_hash,
                )
            )
            if device is None:
                device = Device(
                    user_id=user.id,
                    device_key_hash=admin_key_hash,
                    identity_kind="admin_browser",
                    name=body.device_name,
                    platform=body.platform,
                )
                session.add(device)
                session.flush()
        elif body.client_kind == "desktop":
            if not key_hash:
                raise HTTPException(status_code=422, detail="Thiếu định danh thiết bị")
            device = session.scalar(
                select(Device).where(
                    Device.user_id == user.id,
                    Device.hardware_key_hash == key_hash,
                    Device.revoked_at.is_(None),
                )
            )
            if device is None:
                raise HTTPException(
                    status_code=403,
                    detail="Thiết bị này chưa được kích hoạt cho tài khoản",
                )
        else:
            if not key_hash:
                raise HTTPException(status_code=422, detail="Thiếu định danh thiết bị")
            device = session.scalar(
                select(Device).where(
                    Device.user_id == user.id,
                    Device.device_key_hash == key_hash,
                    Device.revoked_at.is_(None),
                )
            )
            if device is None:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Thiết bị hoặc trình duyệt này chưa được kích hoạt cho tài khoản. "
                        "Tài khoản chỉ được đăng nhập trên đúng thiết bị đã nhập Key."
                    ),
                )
        if device.revoked_at:
            raise HTTPException(status_code=403, detail="Thiết bị đã bị thu hồi")
        device.last_seen_at = utcnow()
    set_session_cookies(response, user, device, request=request)
    return {
        "user_id": user.id,
        "role": user.role,
        "display_name": user.display_name,
        "registered": True,
    }


@router.get("/auth/device-status")
def device_activation_status(request: Request) -> dict[str, bool]:
    device_key = request.headers.get("x-examify-device-key", "")
    if len(device_key) < 16:
        return {"activated": False}
    key_hash = sha256(device_key)
    with session_scope() as session:
        device = session.scalar(
            select(Device).where(
                or_(
                    Device.device_key_hash == key_hash,
                    Device.hardware_key_hash == key_hash,
                ),
                Device.revoked_at.is_(None),
            )
        )
        return {"activated": device is not None}


@router.get("/auth/state")
def auth_state(request: Request, response: Response) -> dict[str, Any]:
    try:
        identity = current_identity(request, required=False)
    except HTTPException:
        refreshed = identity_from_refresh(request)
        if refreshed is None:
            identity = None
            clear_session_cookies(response)
        else:
            identity, access = refreshed
            request.state.identity = identity
            set_access_cookie(response, access, request=request)
    if identity:
        active_class_count = 0
        with session_scope() as session:
            data_epoch_row = session.get(SystemState, "data_epoch")
            data_epoch = data_epoch_row.value if data_epoch_row else None
            if identity["role"] == "student":
                active_class_count = session.scalar(
                    select(func.count(ClassMember.id))
                    .join(Classroom, Classroom.id == ClassMember.classroom_id)
                    .where(
                        ClassMember.user_id == identity["user_id"],
                        ClassMember.status == "active",
                        Classroom.status == "active",
                    )
                ) or 0
        if identity.get("registered"):
            return {
                "state": "authenticated",
                "authenticated": True,
                "role": identity["role"],
                "user": {
                    "id": identity["user_id"],
                    "display_name": identity["display_name"],
                    "email": identity["email"],
                },
                "active_class_count": active_class_count,
                "data_epoch": data_epoch,
            }
        with session_scope() as session:
            token = session.scalar(
                select(ActivationToken)
                .join(Device, Device.activation_token_id == ActivationToken.id)
                .where(
                    ActivationToken.owner_user_id == identity["user_id"],
                    or_(
                        Device.id == identity["device_id"],
                        ActivationToken.redeemed_by_device_id == identity["device_id"],
                    ),
                )
                .order_by(ActivationToken.redeemed_at.desc())
            )
            if token:
                setup = issue_onboarding_token(
                    identity["user_id"], identity["device_id"], token.id
                )
                set_onboarding_cookie(response, setup, request=request)
                return {
                    "state": "registration_required",
                    "authenticated": False,
                    "role": identity["role"],
                    "active_class_count": active_class_count,
                }
    raw_setup = request.cookies.get("smart_exam_onboarding", "")
    if raw_setup:
        try:
            claims = decode_onboarding(raw_setup)
            with session_scope() as session:
                user = session.get(User, claims.get("sub"))
                if user and not user.registered_at:
                    return {
                        "state": "registration_required",
                        "authenticated": False,
                        "role": user.role,
                        "active_class_count": 0,
                    }
        except HTTPException:
            clear_onboarding_cookie(response)
    return {
        "state": "activation_required",
        "authenticated": False,
        "role": None,
        "active_class_count": 0,
    }


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    from auth_service import REFRESH_COOKIE
    from models import RefreshToken

    identity = current_identity(request, required=False)
    owner_user_id = identity["user_id"] if identity else None
    raw_refresh = request.cookies.get(REFRESH_COOKIE)
    with session_scope() as session:
        if raw_refresh:
            token = session.scalar(
                select(RefreshToken).where(
                    RefreshToken.token_hash == sha256(raw_refresh)
                )
            )
            if token:
                if owner_user_id is None:
                    device = session.get(Device, token.device_id)
                    owner_user_id = device.user_id if device else None
                token.revoked_at = utcnow()
        if owner_user_id:
            abandon_pending_components(
                session,
                owner_user_id=owner_user_id,
            )
    clear_session_cookies(response)
    return {"ok": True}


@router.delete("/full-test-components/{exam_id}")
def abandon_full_test_component(exam_id: str, request: Request) -> dict[str, bool]:
    """Idempotently abandon one unpublished component owned by this user."""

    identity = current_identity(request)
    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        if exam is None or exam.owner_user_id != identity["user_id"]:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiên Full Test")
        if exam.status == "component_pending" and exam.deleted_at is None:
            abandon_pending_components(
                session,
                owner_user_id=identity["user_id"],
                exam_ids={exam_id},
            )
        elif exam.status != "component_abandoned":
            raise HTTPException(
                status_code=409,
                detail="Đề thành phần đã hoàn tất hoặc không thể hủy.",
            )
    return {"ok": True}


@router.post("/auth/refresh")
def refresh_session(request: Request, response: Response) -> dict[str, Any]:
    refreshed = identity_from_refresh(request)
    if refreshed is None:
        clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hạn")
    identity, access = refreshed
    set_access_cookie(response, access, request=request)
    return identity


@router.get("/auth/me")
def me(request: Request) -> dict[str, Any]:
    return current_identity(request)


@router.get("/exams")
def list_exams(
    request: Request,
    kind: str | None = None,
    search: str | None = None,
    archived: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    identity = current_identity(request)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    with session_scope() as session:
        query = select(Exam).where(
            Exam.owner_user_id == identity["user_id"],
            Exam.deleted_at.is_(None),
            Exam.status != "component_pending",
            Exam.archived_at.is_not(None) if archived else Exam.archived_at.is_(None),
        )
        if kind:
            query = query.where(Exam.exam_type == kind)
        if search:
            pattern = f"%{search.strip()}%"
            query = query.where(
                or_(Exam.title.ilike(pattern), Exam.category.ilike(pattern))
            )
        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = session.scalars(
            query.order_by(Exam.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        attempt_rows = session.execute(
            select(Attempt.exam_id, func.count(Attempt.id), func.max(Attempt.submitted_at))
            .where(Attempt.exam_id.in_([row.id for row in rows]))
            .group_by(Attempt.exam_id)
        ).all()
        attempts = {row[0]: {"count": row[1], "last_at": row[2]} for row in attempt_rows}
        return {
            "items": [
                {
                    "id": row.id,
                    "slug": row.slug,
                    "client_exam_id": row.client_exam_id,
                    "job_id": row.job_id,
                    "title": row.title,
                    "category": row.category,
                    "exam_type": row.exam_type,
                    "status": row.status,
                    "question_count": row.question_count,
                    "answer_key_count": row.answer_key_count,
                    "duration_minutes": row.duration_minutes,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "attempt_count": attempts.get(row.id, {}).get("count", 0),
                    "last_attempt_at": attempts.get(row.id, {}).get("last_at"),
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


@router.post("/exams/combine")
def combine_exams(body: CombineExamsRequest, request: Request) -> dict[str, Any]:
    identity = current_identity(request)
    with session_scope() as session:
        listening = session.get(Exam, body.listening_exam_id)
        reading = session.get(Exam, body.reading_exam_id)
        for exam in (listening, reading):
            if (
                exam is None
                or exam.owner_user_id != identity["user_id"]
            ):
                raise HTTPException(status_code=404, detail="Không tìm thấy đề thành phần")
        listening_payload = dict(listening.payload or {})
        reading_payload = dict(reading.payload or {})
        component_jobs = {
            "listening": str(listening_payload.get("job_id") or listening.job_id or ""),
            "reading": str(reading_payload.get("job_id") or reading.job_id or ""),
        }
        # A response may be lost after the database commit. Return the already
        # assembled exam when the browser retries the same pair instead of
        # creating another Full Test or failing because its components were
        # soft-deleted by the first successful request.
        existing_combined = next(
            (
                exam
                for exam in session.scalars(
                    select(Exam).where(
                        Exam.owner_user_id == identity["user_id"],
                        Exam.exam_type == "combined",
                        Exam.deleted_at.is_(None),
                    )
                )
                if dict((exam.payload or {}).get("component_job_ids") or {})
                == component_jobs
            ),
            None,
        )
        if existing_combined is not None:
            # The first combine request commits the durable combined exam
            # before moving component assets.  If the browser disconnected or
            # a later step failed, a retry reaches this idempotency branch with
            # the two components still staged.  Finish that cleanup now so the
            # retry is equivalent to a successful first request.
            if any(
                component.deleted_at is None
                or component.status != "combined_component"
                for component in (listening, reading)
            ):
                session.execute(
                    delete(Asset).where(Asset.exam_id == existing_combined.id)
                )
                session.execute(
                    update(Asset)
                    .where(
                        Asset.exam_id.in_(
                            [body.listening_exam_id, body.reading_exam_id]
                        )
                    )
                    .values(exam_id=existing_combined.id)
                )
                for component in (listening, reading):
                    component.deleted_at = component.deleted_at or utcnow()
                    component.status = "combined_component"
                    component.shared_title_key = None
                if existing_combined.current_version_id is None:
                    from classroom_api import _snapshot_exam

                    version = _snapshot_exam(
                        session, existing_combined, identity["user_id"]
                    )
                    existing_combined.current_version_id = version.id
            existing_payload = dict(existing_combined.payload or {})
            existing_payload["exam_id"] = existing_combined.id
            return existing_payload
        if listening.deleted_at is not None or reading.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Không tìm thấy đề thành phần")
    questions = sorted(
        (listening_payload.get("questions") or [])
        + (reading_payload.get("questions") or []),
        key=lambda item: int(item["number"]),
    )
    if {
        int(item.get("number", 0))
        for item in listening_payload.get("questions") or []
    } != set(range(1, 101)):
        raise HTTPException(status_code=422, detail="Listening phải có đủ câu 1-100")
    if {
        int(item.get("number", 0))
        for item in reading_payload.get("questions") or []
    } != set(range(101, 201)):
        raise HTTPException(status_code=422, detail="Reading phải có đủ câu 101-200")
    stimuli = (listening_payload.get("stimuli") or []) + (
        reading_payload.get("stimuli") or []
    )
    audios = listening_payload.get("audios") or (
        [listening_payload["audio"]] if listening_payload.get("audio") else []
    )
    solutions = (listening_payload.get("solutions") or []) + (
        reading_payload.get("solutions") or []
    )
    payload = {
        "schema_version": 2,
        "job_id": f"{listening_payload.get('job_id', '')}+{reading_payload.get('job_id', '')}",
        "component_job_ids": {
            "listening": str(listening_payload.get("job_id", "")),
            "reading": str(reading_payload.get("job_id", "")),
        },
        "exam_type": "combined",
        "requested_count": len(questions),
        "returned_count": len(questions),
        "total": len(questions),
        "questions": questions,
        "stimuli": stimuli,
        "audio": next((item for item in audios if item.get("part") == "full"), None),
        "audios": audios,
        "solutions": solutions,
        "title": body.title,
        "category": body.category.strip(),
    }
    exam_id = persist_final_exam(
        payload,
        job_id=None,
        owner_user_id=identity["user_id"],
        title=body.title,
        category=body.category.strip(),
        quota_replacement_exam_ids=(
            body.listening_exam_id,
            body.reading_exam_id,
        ),
        defer_version_snapshot=True,
    )
    payload["exam_id"] = exam_id
    if exam_id:
        with session_scope() as session:
            combined = session.get(Exam, exam_id)
            if combined is None:
                raise HTTPException(status_code=409, detail="Đề Full Test đã thay đổi")
            combined.payload = payload
            combined.category = body.category.strip()
            # persist_final_exam cannot know the source object keys when combining
            # two server exams and initially normalizes their public API URLs.
            # Remove those placeholder rows before moving the durable component
            # assets onto the combined exam.
            session.execute(delete(Asset).where(Asset.exam_id == exam_id))
            for component_id in (body.listening_exam_id, body.reading_exam_id):
                component = session.get(Exam, component_id)
                if component:
                    component.deleted_at = utcnow()
                    component.status = "combined_component"
                    component.shared_title_key = None
            session.execute(
                update(Asset)
                .where(
                    Asset.exam_id.in_(
                        [body.listening_exam_id, body.reading_exam_id]
                    )
                )
                .values(exam_id=exam_id)
            )
            for component_name, component_id in (
                ("listening", body.listening_exam_id),
                ("reading", body.reading_exam_id),
            ):
                component_source = session.scalar(
                    select(ExamSource).where(ExamSource.exam_id == component_id)
                )
                if component_source is None:
                    continue
                destination = f"examify-sources/{exam_id}/{component_name}.pdf"
                source = session.scalar(
                    select(ExamSource).where(
                        ExamSource.exam_id == exam_id,
                        ExamSource.component == component_name,
                    )
                )
                # ``persist_final_exam`` has already copied component job
                # sources for a combined payload and committed the destination
                # ExamSource rows.  Re-adding the same (exam_id, component)
                # here used to trigger uq_exam_source_component and return a
                # 500 after the exam itself had already been persisted.  Reuse
                # the row when it exists and only copy when the object has not
                # already reached its final key.
                if storage is not None and not (
                    source is not None
                    and source.bucket == settings.minio_bucket_sources
                    and source.object_key == destination
                ):
                    storage.copy_object(
                        component_source.bucket,
                        component_source.object_key,
                        destination,
                    )
                if source is None:
                    source = ExamSource(
                        exam_id=exam_id,
                        component=component_name,
                    )
                    session.add(source)
                source.bucket = settings.minio_bucket_sources
                source.object_key = destination
                source.filename = f"{component_name}.pdf"
                source.content_type = component_source.content_type
                source.size = component_source.size
                source.sha256 = component_source.sha256
            from classroom_api import _snapshot_exam

            combined_version = _snapshot_exam(session, combined, identity["user_id"])
            combined.current_version_id = combined_version.id
    return payload


@router.post("/exams/{exam_id}/edit")
def open_full_test_edit(exam_id: str, request: Request) -> dict[str, Any]:
    from job_store import store as job_store

    identity = current_identity(request)
    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        if (
            exam is None
            or exam.owner_user_id != identity["user_id"]
            or exam.deleted_at is not None
            or exam.exam_type != "combined"
        ):
            raise HTTPException(status_code=404, detail="Không tìm thấy Full Test")
        payload = dict(exam.payload or {})
        component_jobs = dict(payload.get("component_job_ids") or {})
        if not component_jobs:
            legacy = str(payload.get("job_id") or "").split("+", 1)
            if len(legacy) == 2:
                components = [session.get(Exam, component_id) for component_id in legacy]
                if all(components):
                    component_jobs = {
                        "listening": str(components[0].job_id or ""),
                        "reading": str(components[1].job_id or ""),
                    }
    if not component_jobs.get("listening") or not component_jobs.get("reading"):
        raise HTTPException(status_code=422, detail="Full Test thiếu dữ liệu hai phần")
    try:
        for job_id in component_jobs.values():
            if job_store.owner_id(job_id) not in {None, identity["user_id"]}:
                raise HTTPException(status_code=403, detail="Không có quyền sửa job")
            job_store.read(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=410,
            detail="Dữ liệu OCR của Full Test không còn trên máy chủ",
        ) from exc
    return {
        "exam_id": exam_id,
        "title": payload.get("title") or "",
        "category": payload.get("category") or "",
        "component_job_ids": component_jobs,
    }


@router.post("/exams/{exam_id}/edit/finalize")
def finalize_full_test_edit(
    exam_id: str, body: FullTestEditFinalizeRequest, request: Request
) -> dict[str, Any]:
    from job_store import store as job_store

    identity = current_identity(request)
    try:
        listening = job_store.read(body.listening_job_id)
        reading = job_store.read(body.reading_job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="Phiên chỉnh sửa đã hết hạn") from exc
    if (
        job_store.owner_id(body.listening_job_id) not in {None, identity["user_id"]}
        or job_store.owner_id(body.reading_job_id) not in {None, identity["user_id"]}
    ):
        raise HTTPException(status_code=403, detail="Không có quyền sửa job")
    listening_numbers = {
        int(item.get("number", 0)) for item in listening.get("questions") or []
    }
    reading_numbers = {
        int(item.get("number", 0)) for item in reading.get("questions") or []
    }
    if listening_numbers != set(range(1, 101)) or reading_numbers != set(
        range(101, 201)
    ):
        raise HTTPException(status_code=422, detail="Full Test phải có đủ 200 câu")
    audios = listening.get("audios") or (
        [listening["audio"]] if listening.get("audio") else []
    )
    title = body.title.strip()
    category = body.category.strip()
    questions = sorted(
        (listening.get("questions") or []) + (reading.get("questions") or []),
        key=lambda item: int(item["number"]),
    )
    payload = {
        "schema_version": 2,
        "job_id": f"{body.listening_job_id}+{body.reading_job_id}",
        "component_job_ids": {
            "listening": body.listening_job_id,
            "reading": body.reading_job_id,
        },
        "exam_type": "combined",
        "requested_count": 200,
        "returned_count": 200,
        "total": 200,
        "questions": questions,
        "stimuli": (listening.get("stimuli") or []) + (reading.get("stimuli") or []),
        "audio": next((item for item in audios if item.get("part") == "full"), None),
        "audios": audios,
        "title": title,
        "category": category,
        "exam_id": exam_id,
    }
    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        if (
            exam is None
            or exam.owner_user_id != identity["user_id"]
            or exam.deleted_at is not None
            or exam.exam_type != "combined"
        ):
            raise HTTPException(status_code=404, detail="Không tìm thấy Full Test")
        exam.title = title
        exam.category = category
        exam.question_count = 200
        exam.answer_key_count = sum(
            1 for question in questions if question.get("correct")
        )
        exam.duration_minutes = 120
        exam.payload = payload
    return payload


@router.get("/exams/{exam_id}")
def get_exam(exam_id: str, request: Request) -> dict[str, Any]:
    identity = current_identity(request)
    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        if (
            exam is None
            or exam.owner_user_id != identity["user_id"]
            or exam.deleted_at is not None
            or exam.status == "component_pending"
        ):
            raise HTTPException(status_code=404, detail="Không tìm thấy đề thi")
        return _exam_payload(exam, session)


@router.patch("/exams/{exam_id}")
def update_exam(exam_id: str, body: ExamUpdateRequest, request: Request) -> dict[str, Any]:
    identity = current_identity(request)
    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        if exam is None or exam.owner_user_id != identity["user_id"]:
            raise HTTPException(status_code=404, detail="Không tìm thấy đề thi")
        if body.title is not None:
            exam.title = body.title.strip()
        if body.category is not None:
            exam.category = body.category.strip()
        if body.archived is not None:
            exam.archived_at = utcnow() if body.archived else None
        session.flush()
        return _exam_payload(exam)


@router.delete("/exams/{exam_id}")
def delete_exam(exam_id: str, request: Request) -> dict[str, bool]:
    identity = current_identity(request)
    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        if exam is None or exam.owner_user_id != identity["user_id"]:
            raise HTTPException(status_code=404, detail="Không tìm thấy đề thi")
        exam.deleted_at = utcnow()
    return {"ok": True}


@router.post("/exams/{exam_id}/attempts")
def create_attempt(
    exam_id: str, body: AttemptCreateRequest, request: Request
) -> dict[str, Any]:
    identity = current_identity(request)
    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        if (
            exam is None
            or exam.owner_user_id != identity["user_id"]
            or exam.deleted_at is not None
            or exam.status == "component_pending"
        ):
            raise HTTPException(status_code=404, detail="Không tìm thấy đề thi")
        duration = body.duration_seconds or exam.duration_minutes * 60
        deadline = utcnow() + timedelta(seconds=duration)
        attempt = Attempt(
            exam_id=exam.id,
            exam_version_id=exam.current_version_id,
            user_id=identity["user_id"],
            duration_seconds=duration,
            time_left_seconds=duration,
            deadline_at=deadline,
        )
        session.add(attempt)
        session.flush()
        return {
            "attempt_id": attempt.id,
            "exam": _exam_payload(exam, session),
            "answers": {},
            "duration_seconds": duration,
            "time_left_seconds": duration,
            "current_question_number": None,
            "deadline_at": deadline,
            "exam_content_hash": sha256(
                json.dumps(
                    exam.payload or {},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
            "accepted_revision": attempt.answer_revision,
        }


def _owned_attempt(
    session: Any,
    attempt_id: str,
    user_id: str,
    *,
    lock: bool = False,
) -> Attempt:
    attempt = session.get(Attempt, attempt_id, with_for_update=lock)
    if attempt is None or attempt.user_id != user_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy lượt làm bài")
    return attempt


@router.get("/attempts/history")
def attempt_history(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    # This static route must be registered before /attempts/{attempt_id};
    # otherwise Starlette treats the literal word "history" as an attempt ID.
    return _attempt_history_page(request, page, page_size)


@router.get("/attempts/{attempt_id}")
def get_attempt(attempt_id: str, request: Request) -> dict[str, Any]:
    identity = current_identity(request)
    with session_scope() as session:
        attempt = _owned_attempt(session, attempt_id, identity["user_id"])
        exam = session.get(Exam, attempt.exam_id)
        version = _attempt_version(session, attempt)
        answers = {
            item.question_number: item.selected
            for item in session.scalars(
                select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id)
            )
        }
        return {
            "id": attempt.id,
            "status": attempt.status,
            "duration_seconds": attempt.duration_seconds,
            "time_left_seconds": attempt.time_left_seconds,
            "current_question_number": attempt.current_question_number,
            "answers": answers,
            "exam": _attempt_exam_payload(
                session,
                attempt,
                exam,
                reveal_answers=attempt.status == "submitted",
            ),
            "submitted_at": attempt.submitted_at,
            "accepted_revision": attempt.answer_revision,
            "has_solutions": bool(
                (
                    (version.payload if version is not None else exam.payload if exam else {})
                    or {}
                ).get("solutions")
            ),
        }


def _store_personal_answers(
    session: Any,
    attempt: Attempt,
    body: AttemptAnswersRequest,
    allowed_numbers: set[int],
    *,
    force: bool = False,
    correct_by_number: dict[int, str | None] | None = None,
) -> dict[int, str] | None:
    current_revision = int(attempt.answer_revision or 0)
    if (
        not force
        and body.client_revision is not None
        and body.client_revision <= current_revision
    ):
        return None
    try:
        answers = normalize_attempt_answers(body.answers, allowed_numbers)
    except InvalidAttemptAnswer as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    replace_attempt_answers(
        session,
        attempt.id,
        answers,
        correct_by_number=correct_by_number,
    )
    attempt.time_left_seconds = min(
        attempt.time_left_seconds,
        body.time_left_seconds,
        attempt.duration_seconds,
    )
    attempt.answered_count = len(answers)
    attempt.answer_revision = (
        max(current_revision + 1, body.client_revision)
        if body.client_revision is not None
        else current_revision + 1
    )
    attempt.updated_at = utcnow()
    return answers


def _personal_attempt_result(
    session: Any,
    attempt: Attempt,
    exam: Exam | None,
    answers: list[AttemptAnswer] | None = None,
    answer_snapshot: dict[int, str] | None = None,
) -> dict[str, Any]:
    stored_answers = answers
    if answer_snapshot is None and stored_answers is None:
        stored_answers = session.scalars(
            select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id)
        ).all()
    version = _attempt_version(session, attempt)
    return {
        "attempt_id": attempt.id,
        "schema_version": 2,
        "exam": _attempt_exam_payload(
            session, attempt, exam, reveal_answers=True
        ),
        "answers": answer_snapshot
        if answer_snapshot is not None
        else {
            answer.question_number: answer.selected for answer in (stored_answers or [])
        },
        "duration_seconds": attempt.duration_seconds,
        "time_left_seconds": attempt.time_left_seconds,
        "submitted_at": attempt.submitted_at,
        "status": attempt.status,
        "accepted_revision": attempt.answer_revision,
        "receipt_id": attempt.submit_receipt_id,
        "has_solutions": bool(
            (
                (version.payload if version is not None else exam.payload if exam else {})
                or {}
            ).get("solutions")
        ),
    }


@router.patch("/attempts/{attempt_id}/answers")
def save_attempt_answers(
    attempt_id: str, body: AttemptAnswersRequest, request: Request
) -> dict[str, Any]:
    identity = current_identity(request)
    with session_scope() as session:
        attempt = _owned_attempt(
            session, attempt_id, identity["user_id"], lock=True
        )
        if attempt.status != "in_progress":
            return {
                "ok": True,
                "status": attempt.status,
                "accepted_revision": attempt.answer_revision,
            }
        allowed_numbers = {number for number, _part, _correct in _attempt_question_rows(session, attempt)}
        _store_personal_answers(session, attempt, body, allowed_numbers)
        return {
            "ok": True,
            "status": attempt.status,
            "accepted_revision": attempt.answer_revision,
        }


@router.get("/attempts/{attempt_id}/state")
def get_attempt_state(attempt_id: str, request: Request) -> dict[str, Any]:
    """Reload only mutable attempt state; the immutable exam is browser-cached."""

    identity = current_identity(request)
    with session_scope() as session:
        attempt = _owned_attempt(session, attempt_id, identity["user_id"])
        return {
            "attempt_id": attempt.id,
            "status": attempt.status,
            "answers": canonical_answers(session, attempt.id),
            "accepted_revision": attempt.answer_revision,
            "time_left_seconds": attempt.time_left_seconds,
            "current_question_number": attempt.current_question_number,
            "deadline_at": attempt.deadline_at,
            "submitted_at": attempt.submitted_at,
            "receipt_id": attempt.submit_receipt_id,
        }


@router.patch("/attempts/{attempt_id}/sync")
def sync_attempt(
    attempt_id: str, body: AttemptSyncRequest, request: Request
) -> dict[str, Any]:
    identity = current_identity(request)
    with session_scope() as session:
        attempt = _owned_attempt(
            session, attempt_id, identity["user_id"], lock=True
        )
        if attempt.status != "in_progress":
            return {
                "accepted_revision": attempt.answer_revision,
                "accepted_batch_id": str(body.batch_id),
                "server_time": utcnow(),
                "deadline_at": attempt.deadline_at,
                "status": attempt.status,
                "receipt_id": attempt.submit_receipt_id,
            }
        allowed_numbers = {number for number, _part, _correct in _attempt_question_rows(session, attempt)}
        try:
            result = sync_attempt_changes(
                session,
                attempt,
                batch_id=str(body.batch_id),
                base_revision=body.base_revision,
                raw_changes=body.changes,
                allowed_numbers=allowed_numbers,
                time_left_seconds=body.time_left_seconds,
            )
        except InvalidAttemptAnswer as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AttemptBatchReuse as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AttemptRevisionConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": str(exc),
                    "server_revision": attempt.answer_revision,
                    "answers": canonical_answers(session, attempt.id),
                },
            ) from exc
        attempt.answered_count = body.presence.answered_count
        attempt.current_question_number = body.presence.current_question_number
        attempt.is_fullscreen = body.presence.is_fullscreen
        attempt.visibility_state = body.presence.visibility_state
        attempt.last_heartbeat_at = utcnow()
        presence_store.put(
            attempt.id,
            {
                "attempt_id": attempt.id,
                "answered_count": attempt.answered_count,
                "current_question_number": body.presence.current_question_number,
                "time_left_seconds": attempt.time_left_seconds,
                "is_fullscreen": body.presence.is_fullscreen,
                "visibility_state": body.presence.visibility_state,
                "last_heartbeat_at": utcnow().isoformat(),
            },
        )
        return {
            "accepted_revision": result.accepted_revision,
            "accepted_batch_id": result.accepted_batch_id,
            "duplicate": result.duplicate,
            "server_time": utcnow(),
            "deadline_at": attempt.deadline_at,
            "status": attempt.status,
        }


@router.post("/attempts/{attempt_id}/submit")
def submit_attempt(
    attempt_id: str, body: AttemptAnswersRequest, request: Request
) -> dict[str, Any]:
    identity = current_identity(request)
    with session_scope() as session:
        attempt = _owned_attempt(
            session, attempt_id, identity["user_id"], lock=True
        )
        # Version-backed attempts no longer need the mutable Exam row in the
        # grading hot path. This keeps peak submission at the previous query
        # budget while preserving a fallback for pre-migration attempts.
        exam = (
            None
            if attempt.exam_version_id
            else session.get(Exam, attempt.exam_id)
        )
        if exam is None and not attempt.exam_version_id:
            raise HTTPException(status_code=404, detail="Không tìm thấy đề thi")
        if attempt.status == "submitted":
            return _personal_attempt_result(session, attempt, exam)
        question_rows = _attempt_question_rows(session, attempt)
        questions = {number: correct for number, _part, correct in question_rows}
        correct_by_number = dict(questions)
        selected = _store_personal_answers(
            session,
            attempt,
            body,
            set(questions),
            force=True,
            correct_by_number=correct_by_number,
        )
        assert selected is not None
        correct = 0
        graded = 0
        for number, answer in selected.items():
            correct_answer = questions.get(number)
            if correct_answer:
                graded += 1
                correct += int(correct_answer == answer)
        attempt.status = "submitted"
        attempt.correct_count = correct
        attempt.graded_count = graded
        listening_questions = [
            (number, correct_answer)
            for number, _part, correct_answer in question_rows
            if number <= 100 and correct_answer
        ]
        reading_questions = [
            (number, correct_answer)
            for number, _part, correct_answer in question_rows
            if number >= 101 and correct_answer
        ]
        listening_correct = sum(
            selected.get(number) == correct_answer
            for number, correct_answer in listening_questions
        )
        reading_correct = sum(
            selected.get(number) == correct_answer
            for number, correct_answer in reading_questions
        )
        (
            attempt.listening_score,
            attempt.reading_score,
            attempt.score_toeic,
        ) = toeic_scores(
            listening_correct,
            len(listening_questions),
            reading_correct,
            len(reading_questions),
        )
        attempt.time_spent_seconds = max(
            0, attempt.duration_seconds - (attempt.time_left_seconds or 0)
        )
        attempt.submit_reason = "manual"
        attempt.submitted_at = utcnow()
        attempt.answered_count = len(selected)
        attempt.submit_receipt_id = attempt.submit_receipt_id or uuid4()
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if len(idempotency_key) > 80:
            raise HTTPException(status_code=422, detail="Idempotency-Key quá dài")
        attempt.submit_idempotency_key = idempotency_key or None
        attempt.submitted_answer_hash = sha256(
            json.dumps(
                {str(number): selected[number] for number in sorted(selected)},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return _personal_attempt_result(
            session,
            attempt,
            exam,
            answer_snapshot=selected,
        )


def _attempt_history_page(
    request: Request,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    identity = current_identity(request)
    with session_scope() as session:
        filters = (
            Attempt.user_id == identity["user_id"],
            Attempt.status == "submitted",
        )
        total_attempts = int(
            session.scalar(select(func.count(Attempt.id)).where(*filters)) or 0
        )
        rows = session.execute(
            select(Attempt, Exam, ExamVersion)
            .join(Exam, Exam.id == Attempt.exam_id)
            .outerjoin(ExamVersion, ExamVersion.id == Attempt.exam_version_id)
            .where(*filters)
            .order_by(Attempt.submitted_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()

        legacy_rows = [
            (attempt, exam, version)
            for attempt, exam, version in rows
            if attempt.listening_score is None
            or attempt.reading_score is None
            or attempt.score_toeic is None
        ]
        exam_ids = {
            exam.id for _attempt, exam, version in legacy_rows if version is None
        }
        version_ids = {
            version.id
            for _attempt, _exam, version in legacy_rows
            if version is not None
        }
        attempt_ids = {attempt.id for attempt, _exam, _version in legacy_rows}
        questions_by_exam: dict[str, list[tuple[int, str]]] = {}
        questions_by_version: dict[str, list[tuple[int, str]]] = {}
        if exam_ids:
            for exam_id, number, correct_answer in session.execute(
                select(
                    QuestionRecord.exam_id,
                    QuestionRecord.number,
                    QuestionRecord.correct,
                ).where(QuestionRecord.exam_id.in_(exam_ids))
            ):
                if correct_answer:
                    questions_by_exam.setdefault(exam_id, []).append(
                        (number, correct_answer)
                    )
        if version_ids:
            for version_id, number, correct_answer in session.execute(
                select(
                    ExamVersionQuestion.exam_version_id,
                    ExamVersionQuestion.question_number,
                    ExamVersionQuestion.correct,
                ).where(ExamVersionQuestion.exam_version_id.in_(version_ids))
            ):
                if correct_answer:
                    questions_by_version.setdefault(version_id, []).append(
                        (number, correct_answer)
                    )
        answers_by_attempt: dict[str, dict[int, str]] = {}
        if attempt_ids:
            for stored_attempt_id, question_number, selected_answer in session.execute(
                select(
                    AttemptAnswer.attempt_id,
                    AttemptAnswer.question_number,
                    AttemptAnswer.selected,
                ).where(AttemptAnswer.attempt_id.in_(attempt_ids))
            ):
                answers_by_attempt.setdefault(stored_attempt_id, {})[
                    question_number
                ] = selected_answer

        items = []
        for attempt, exam, version in rows:
            correct = attempt.correct_count or 0
            graded_total = attempt.graded_count or exam.question_count or 1
            listening_score = attempt.listening_score
            reading_score = attempt.reading_score
            score_toeic = attempt.score_toeic
            if (
                listening_score is None
                or reading_score is None
                or score_toeic is None
            ):
                question_rows = (
                    questions_by_version.get(version.id, [])
                    if version is not None
                    else questions_by_exam.get(exam.id, [])
                )
                selected = answers_by_attempt.get(attempt.id, {})
                listening_questions = [
                    item for item in question_rows if item[0] <= 100
                ]
                reading_questions = [
                    item for item in question_rows if item[0] >= 101
                ]
                listening_correct = sum(
                    selected.get(number) == correct_answer
                    for number, correct_answer in listening_questions
                )
                reading_correct = sum(
                    selected.get(number) == correct_answer
                    for number, correct_answer in reading_questions
                )
                listening_score, reading_score, score_toeic = toeic_scores(
                    listening_correct,
                    len(listening_questions),
                    reading_correct,
                    len(reading_questions),
                )
            time_spent = attempt.time_spent_seconds
            if time_spent is None:
                time_spent = max(
                    0,
                    attempt.duration_seconds - (attempt.time_left_seconds or 0),
                )

            items.append({
                "id": attempt.id,
                "client_exam_id": exam.id,
                "exam_title": version.title if version is not None else exam.title,
                "exam_type": version.exam_type if version is not None else exam.exam_type,
                "score_toeic": score_toeic,
                "listening_score": listening_score,
                "reading_score": reading_score,
                "correct_count": correct,
                "total_questions": graded_total,
                "duration_seconds": attempt.duration_seconds,
                "time_spent_seconds": time_spent,
                "mode": "practice" if attempt.launch_mode == "practice" else "exam",
                "source": "classroom" if attempt.class_assignment_id else "bank",
                "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else utcnow().isoformat(),
                "has_solutions": bool(
                    ((version.payload if version is not None else exam.payload) or {}).get(
                        "solutions"
                    )
                ),
            })
        return {
            "items": items,
            "total": total_attempts,
            "page": page,
            "page_size": page_size,
            "pages": max(
                1, (total_attempts + page_size - 1) // page_size
            ),
        }


def _expire_available_tokens(session: Any) -> None:
    session.execute(
        update(ActivationToken)
        .where(
            ActivationToken.status == "available",
            ActivationToken.expires_at.is_not(None),
            ActivationToken.expires_at <= utcnow(),
        )
        .values(status="expired")
    )


@router.get("/admin/dashboard")
def admin_dashboard(request: Request) -> dict[str, Any]:
    require_admin(request)
    with session_scope() as session:
        _expire_available_tokens(session)
        token_counts = dict(
            session.execute(
                select(ActivationToken.status, func.count(ActivationToken.id)).group_by(
                    ActivationToken.status
                )
            ).all()
        )
        job_counts = dict(
            session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
        )
        return {
            "tokens": token_counts,
            "jobs": job_counts,
            "users": session.scalar(select(func.count(User.id))) or 0,
            "devices": session.scalar(
                select(func.count(Device.id)).where(Device.revoked_at.is_(None))
            )
            or 0,
            "exams": session.scalar(
                select(func.count(Exam.id)).where(Exam.deleted_at.is_(None))
            )
            or 0,
        }


def _token_group_name_key(value: str) -> tuple[str, str]:
    name = " ".join(value.strip().split())
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Tên nhóm phải có ít nhất 2 ký tự")
    return name, name.casefold()


def _token_group_payload(session: Any, group: ActivationTokenGroup) -> dict[str, Any]:
    counts = dict(
        session.execute(
            select(ActivationToken.status, func.count(ActivationToken.id))
            .where(ActivationToken.group_id == group.id)
            .group_by(ActivationToken.status)
        ).all()
    )
    return {
        "id": group.id,
        "name": group.name,
        "total": sum(counts.values()),
        "counts": counts,
        "exportable_count": session.scalar(
            select(func.count(ActivationToken.id)).where(
                ActivationToken.group_id == group.id,
                ActivationToken.encrypted_code.is_not(None),
            )
        )
        or 0,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
    }


@router.get("/admin/token-groups")
def list_token_groups(request: Request) -> dict[str, Any]:
    require_admin(request)
    with session_scope() as session:
        _expire_available_tokens(session)
        groups = session.scalars(
            select(ActivationTokenGroup).order_by(ActivationTokenGroup.name)
        ).all()
        ungrouped_counts = dict(
            session.execute(
                select(ActivationToken.status, func.count(ActivationToken.id))
                .where(ActivationToken.group_id.is_(None))
                .group_by(ActivationToken.status)
            ).all()
        )
        return {
            "items": [_token_group_payload(session, group) for group in groups],
            "ungrouped": {
                "id": None,
                "name": "Chưa phân nhóm",
                "total": sum(ungrouped_counts.values()),
                "counts": ungrouped_counts,
            },
        }


@router.post("/admin/token-groups")
def create_token_group(
    body: TokenGroupCreateRequest, request: Request
) -> dict[str, Any]:
    identity = require_admin(request)
    name, name_key = _token_group_name_key(body.name)
    with session_scope() as session:
        if session.scalar(
            select(ActivationTokenGroup.id).where(
                ActivationTokenGroup.name_key == name_key
            )
        ):
            raise HTTPException(status_code=409, detail="Tên nhóm token đã tồn tại")
        group = ActivationTokenGroup(
            name=name,
            name_key=name_key,
            created_by_user_id=identity["user_id"],
        )
        session.add(group)
        session.flush()
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="activation_group.created",
                target_type="activation_token_group",
                target_id=group.id,
                detail={"name": group.name},
            )
        )
        return _token_group_payload(session, group)


@router.patch("/admin/token-groups/{group_id}")
def update_token_group(
    group_id: str, body: TokenGroupUpdateRequest, request: Request
) -> dict[str, Any]:
    identity = require_admin(request)
    name, name_key = _token_group_name_key(body.name)
    with session_scope() as session:
        group = session.get(ActivationTokenGroup, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy nhóm token")
        duplicate = session.scalar(
            select(ActivationTokenGroup.id).where(
                ActivationTokenGroup.name_key == name_key,
                ActivationTokenGroup.id != group.id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Tên nhóm token đã tồn tại")
        group.name = name
        group.name_key = name_key
        group.updated_at = utcnow()
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="activation_group.renamed",
                target_type="activation_token_group",
                target_id=group.id,
                detail={"name": group.name},
            )
        )
        return _token_group_payload(session, group)


@router.delete("/admin/token-groups/{group_id}")
def delete_token_group(group_id: str, request: Request) -> dict[str, Any]:
    identity = require_admin(request)
    with session_scope() as session:
        group = session.get(ActivationTokenGroup, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy nhóm token")
        moved = session.scalar(
            select(func.count(ActivationToken.id)).where(
                ActivationToken.group_id == group.id
            )
        ) or 0
        session.execute(
            update(ActivationToken)
            .where(ActivationToken.group_id == group.id)
            .values(group_id=None)
        )
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="activation_group.deleted",
                target_type="activation_token_group",
                target_id=group.id,
                detail={"name": group.name, "tokens_moved_to_ungrouped": moved},
            )
        )
        session.delete(group)
        return {"ok": True, "tokens_moved_to_ungrouped": moved}


@router.patch("/admin/tokens/group-membership")
def move_tokens_to_group(
    body: TokenGroupMembershipRequest, request: Request
) -> dict[str, Any]:
    identity = require_admin(request)
    token_ids = list(dict.fromkeys(body.token_ids))
    with session_scope() as session:
        if body.group_id and session.get(ActivationTokenGroup, body.group_id) is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy nhóm token")
        existing_ids = set(
            session.scalars(
                select(ActivationToken.id).where(ActivationToken.id.in_(token_ids))
            ).all()
        )
        if len(existing_ids) != len(token_ids):
            raise HTTPException(status_code=404, detail="Có token không còn tồn tại")
        session.execute(
            update(ActivationToken)
            .where(ActivationToken.id.in_(token_ids))
            .values(group_id=body.group_id)
        )
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="activation.group_membership_changed",
                target_type="activation_token_group",
                target_id=body.group_id,
                detail={"token_ids": token_ids, "count": len(token_ids)},
            )
        )
        return {"ok": True, "count": len(token_ids), "group_id": body.group_id}


@router.delete("/admin/tokens")
def permanently_delete_tokens(
    body: TokenBulkDeleteRequest, request: Request
) -> dict[str, Any]:
    """Permanently remove up to 1,000 selected tokens in one transaction."""
    identity = require_admin(request)
    token_ids = list(dict.fromkeys(body.token_ids))
    with session_scope() as session:
        tokens = session.execute(
            select(
                ActivationToken.id,
                ActivationToken.token_hint,
                ActivationToken.status,
                ActivationToken.group_id,
            ).where(ActivationToken.id.in_(token_ids))
        ).all()
        existing_ids = {row.id for row in tokens}
        if len(existing_ids) != len(token_ids):
            raise HTTPException(
                status_code=404,
                detail="Có token không còn tồn tại; danh sách chưa được xóa",
            )

        # Keep users and their current devices intact. Only detach references
        # to the disposable activation codes before deleting the token rows.
        session.execute(
            update(ActivationToken)
            .where(ActivationToken.parent_token_id.in_(token_ids))
            .values(parent_token_id=None)
        )
        session.execute(
            update(Device)
            .where(Device.activation_token_id.in_(token_ids))
            .values(activation_token_id=None)
        )
        session.execute(
            delete(ActivationToken).where(ActivationToken.id.in_(token_ids))
        )
        status_counts: dict[str, int] = {}
        for token in tokens:
            status_counts[token.status] = status_counts.get(token.status, 0) + 1
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="activation.bulk_permanently_deleted",
                target_type="activation_token",
                detail={
                    "count": len(token_ids),
                    "token_ids": token_ids,
                    "hints": [row.token_hint for row in tokens],
                    "status_counts": status_counts,
                    "group_ids": sorted(
                        {row.group_id for row in tokens if row.group_id}
                    ),
                },
            )
        )
        logger.info(
            "ACTIVATION_TOKENS_BULK_PERMANENTLY_DELETED count=%s actor_user_id=%s",
            len(token_ids),
            identity["user_id"],
        )
    return {"ok": True, "deleted": len(token_ids)}


@router.get("/admin/token-groups/{group_id}/export.xlsx")
def export_token_group(group_id: str, request: Request) -> StreamingResponse:
    identity = require_admin(request)
    with session_scope() as session:
        _expire_available_tokens(session)
        group = session.get(ActivationTokenGroup, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy nhóm token")
        tokens = session.scalars(
            select(ActivationToken)
            .where(ActivationToken.group_id == group.id)
            .order_by(ActivationToken.created_at)
        ).all()
        status_labels = {
            "available": "Chưa dùng",
            "redeemed": "Đã kích hoạt",
            "revoked": "Đã thu hồi",
            "expired": "Hết hạn",
        }
        rows: list[dict[str, Any]] = []
        for token in tokens:
            owner = session.get(User, token.owner_user_id) if token.owner_user_id else None
            code: str | None = None
            note = ""
            if token.encrypted_code:
                try:
                    code = decrypt_activation_code(
                        token.encrypted_code, token.id, settings.token_export_secret
                    )
                except TokenExportUnavailable:
                    note = "Không thể giải mã; kiểm tra TOKEN_EXPORT_SECRET"
            else:
                note = "Token cũ chỉ còn hash, không thể khôi phục mã đầy đủ"
            rows.append(
                {
                    "code": code or f"••••-{token.token_hint}",
                    "exportable": bool(code),
                    "group_name": group.name,
                    "role": token.assigned_role,
                    "status": status_labels.get(token.status, token.status),
                    "status_key": token.status,
                    "owner_name": owner.display_name if owner else token.label,
                    "owner_email": owner.email if owner else "",
                    "created_at": token.created_at.isoformat() if token.created_at else "",
                    "expires_at": token.expires_at.isoformat() if token.expires_at else "",
                    "redeemed_at": token.redeemed_at.isoformat() if token.redeemed_at else "",
                    "device_count": session.scalar(
                        select(func.count(Device.id)).where(
                            Device.activation_token_id == token.id,
                            Device.revoked_at.is_(None),
                        )
                    )
                    or 0,
                    "export_note": note,
                }
            )
        workbook = build_token_workbook(rows)
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="activation_group.exported",
                target_type="activation_token_group",
                target_id=group.id,
                detail={"count": len(tokens)},
            )
        )
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", group.name).strip("-") or group.id
        utf8_name = quote(f"{group.name}-tokens.xlsx")
        return StreamingResponse(
            BytesIO(workbook),
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{safe_name}-tokens.xlsx"; '
                    f"filename*=UTF-8''{utf8_name}"
                )
            },
        )


@router.post("/admin/tokens")
def create_tokens(
    body: TokenCreateRequest, request: Request
) -> dict[str, Any]:
    identity = require_admin(request)
    expires = None
    plain_codes: list[str] = []
    with session_scope() as session:
        group = session.get(ActivationTokenGroup, body.group_id) if body.group_id else None
        if body.group_id and group is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy nhóm token")
        if body.count > 1 and group is None:
            raise HTTPException(
                status_code=422, detail="Sinh hàng loạt cần chọn một nhóm token"
            )
        for _ in range(body.count):
            code = create_activation_code()
            plain_codes.append(code)
            normalized = normalize_activation_code(code)
            token_id = uuid4()
            session.add(
                ActivationToken(
                    id=token_id,
                    token_hash=sha256(normalized),
                    token_hint=normalized[-8:],
                    encrypted_code=encrypt_activation_code(
                        code, token_id, settings.token_export_secret
                    ),
                    group_id=group.id if group else None,
                    label=body.label if body.count == 1 else "",
                    note=body.note,
                    assigned_role=(
                        body.assigned_role
                        if body.assigned_role in {"user", "teacher", "student"}
                        else "user"
                    ),
                    exam_limit=0 if body.assigned_role == "student" else body.exam_limit,
                    max_devices=body.max_devices,
                    expires_at=expires,
                    created_by_user_id=identity["user_id"],
                )
            )
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="activation.created",
                target_type="activation_token",
                detail={
                    "count": body.count,
                    "label": body.label,
                    "assigned_role": body.assigned_role,
                    "exam_limit": body.exam_limit,
                    "max_devices": body.max_devices,
                    "group_id": group.id if group else None,
                },
            )
        )
    return {
        "codes": plain_codes,
        "count": len(plain_codes),
        "shown_once": False,
        "reexportable": True,
        "group": {"id": group.id, "name": group.name} if group else None,
    }


@router.get("/admin/tokens")
def list_tokens(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    group_id: str | None = None,
    status: str | None = None,
    role: str | None = None,
    search: str = "",
) -> dict[str, Any]:
    require_admin(request)
    if status and status not in {"available", "redeemed", "revoked", "expired"}:
        raise HTTPException(status_code=422, detail="Trạng thái token không hợp lệ")
    if role and role not in {"user", "teacher", "student"}:
        raise HTTPException(status_code=422, detail="Vai trò token không hợp lệ")
    safe_page = max(1, page)
    safe_page_size = max(1, min(100, page_size))
    with session_scope() as session:
        _expire_available_tokens(session)
        conditions = []
        if group_id == "ungrouped":
            conditions.append(ActivationToken.group_id.is_(None))
        elif group_id:
            conditions.append(ActivationToken.group_id == group_id)
        if status:
            conditions.append(ActivationToken.status == status)
        if role:
            conditions.append(ActivationToken.assigned_role == role)
        query = search.strip()
        if query:
            pattern = f"%{query}%"
            conditions.append(
                or_(
                    ActivationToken.token_hint.ilike(pattern),
                    ActivationToken.label.ilike(pattern),
                    User.display_name.ilike(pattern),
                    User.email.ilike(pattern),
                )
            )
        base = select(ActivationToken).outerjoin(
            User, User.id == ActivationToken.owner_user_id
        )
        count_query = select(func.count(ActivationToken.id)).outerjoin(
            User, User.id == ActivationToken.owner_user_id
        )
        if conditions:
            base = base.where(*conditions)
            count_query = count_query.where(*conditions)
        total = session.scalar(count_query) or 0
        rows = session.scalars(
            base
            .order_by(ActivationToken.created_at.desc())
            .offset((safe_page - 1) * safe_page_size)
            .limit(safe_page_size)
        ).all()
        return {
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "pages": max(1, (total + safe_page_size - 1) // safe_page_size),
            "items": [
                {
                    "id": row.id,
                    "hint": row.token_hint,
                    "label": row.label,
                    "group_id": row.group_id,
                    "exportable": bool(row.encrypted_code),
                    "assigned_role": row.assigned_role,
                    "status": row.status,
                    "expires_at": row.expires_at,
                    "redeemed_at": row.redeemed_at,
                    "device_id": row.redeemed_by_device_id,
                    "owner_user_id": row.owner_user_id,
                    "owner_name": (
                        session.get(User, row.owner_user_id).display_name
                        if row.owner_user_id and session.get(User, row.owner_user_id)
                        else None
                    ),
                    "owner_email": (
                        session.get(User, row.owner_user_id).email
                        if row.owner_user_id and session.get(User, row.owner_user_id)
                        else None
                    ),
                    "parent_token_id": row.parent_token_id,
                    "exam_count": (
                        session.scalar(
                            select(User.exam_created_count).where(
                                User.id == row.owner_user_id
                            )
                        )
                        if row.owner_user_id
                        else 0
                    ),
                    "exam_limit": row.exam_limit,
                    "max_devices": max(1, min(2, row.max_devices or 1)),
                    "device_count": (
                        session.scalar(
                            select(func.count(Device.id)).where(
                                Device.activation_token_id == row.id,
                                Device.revoked_at.is_(None),
                            )
                        )
                        or (
                            session.scalar(
                                select(func.count(Device.id)).where(
                                    Device.id == row.redeemed_by_device_id,
                                    Device.revoked_at.is_(None),
                                )
                            )
                            or 0
                        )
                    ),
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        }


@router.get("/admin/tokens/{token_id}")
def token_detail(token_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    with session_scope() as session:
        token = session.get(ActivationToken, token_id)
        if token is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy token")
        owner = session.get(User, token.owner_user_id) if token.owner_user_id else None
        devices = (
            session.scalars(
                select(Device)
                .where(
                    Device.user_id == token.owner_user_id,
                    or_(
                        Device.activation_token_id == token.id,
                        Device.id == token.redeemed_by_device_id,
                    ),
                )
                .order_by(Device.activated_at.desc())
            ).all()
            if token.owner_user_id
            else []
        )
        exams = (
            session.scalars(
                select(Exam)
                .where(
                    Exam.owner_user_id == token.owner_user_id,
                    Exam.deleted_at.is_(None),
                )
                .order_by(Exam.updated_at.desc())
            ).all()
            if token.owner_user_id
            else []
        )
        return {
            "id": token.id,
            "hint": token.token_hint,
            "label": token.label,
            "note": token.note,
            "assigned_role": token.assigned_role,
            "status": token.status,
            "exam_limit": token.exam_limit,
            "max_devices": max(1, min(2, token.max_devices or 1)),
            "expires_at": token.expires_at,
            "redeemed_at": token.redeemed_at,
            "owner": (
                {
                    "id": owner.id,
                    "display_name": owner.display_name,
                    "status": owner.status,
                }
                if owner
                else None
            ),
            "devices": [
                {
                    "id": device.id,
                    "name": device.name,
                    "platform": device.platform,
                    "activated_at": device.activated_at,
                    "last_seen_at": device.last_seen_at,
                    "revoked_at": device.revoked_at,
                }
                for device in devices
            ],
            "exams": [
                {
                    "id": exam.id,
                    "title": exam.title,
                    "exam_type": exam.exam_type,
                    "question_count": exam.question_count,
                    "updated_at": exam.updated_at,
                }
                for exam in exams
            ],
        }


@router.get("/admin/users")
def list_users(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    require_admin(request)
    with session_scope() as session:
        device_counts = (
            select(Device.user_id, func.count(Device.id).label("device_count"))
            .where(Device.revoked_at.is_(None))
            .group_by(Device.user_id)
            .subquery()
        )
        exam_counts = (
            select(Exam.owner_user_id, func.count(Exam.id).label("exam_count"))
            .where(Exam.deleted_at.is_(None))
            .group_by(Exam.owner_user_id)
            .subquery()
        )
        token_limits = (
            select(
                ActivationToken.owner_user_id,
                func.max(ActivationToken.max_devices).label("device_limit"),
            )
            .where(ActivationToken.status.in_(["available", "redeemed"]))
            .group_by(ActivationToken.owner_user_id)
            .subquery()
        )
        total = int(session.scalar(select(func.count(User.id))) or 0)
        rows = session.execute(
            select(
                User,
                func.coalesce(device_counts.c.device_count, 0),
                func.coalesce(exam_counts.c.exam_count, 0),
                func.coalesce(token_limits.c.device_limit, 1),
            )
            .outerjoin(device_counts, device_counts.c.user_id == User.id)
            .outerjoin(exam_counts, exam_counts.c.owner_user_id == User.id)
            .outerjoin(token_limits, token_limits.c.owner_user_id == User.id)
            .order_by(User.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {
            "items": [
                {
                    "id": user.id,
                    "display_name": user.display_name,
                    "email": user.email,
                    "role": user.role,
                    "status": user.status,
                    "device_count": int(device_count),
                    "active_exam_count": int(active_exam_count),
                    "exam_count": user.exam_created_count,
                    "exam_limit": user.exam_limit,
                    "device_limit": max(1, min(2, int(device_limit))),
                    "created_at": user.created_at,
                }
                for user, device_count, active_exam_count, device_limit in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }


@router.post("/admin/tokens/{token_id}/reissue")
def reissue_token(
    token_id: str, body: TokenReissueRequest, request: Request
) -> dict[str, Any]:
    """Issue a one-time code for a replacement device without changing ownership."""
    identity = require_admin(request)
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        source = session.get(ActivationToken, token_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy token")

        owner_user_id = source.owner_user_id
        if owner_user_id is None and source.redeemed_by_device_id:
            old_device = session.get(Device, source.redeemed_by_device_id)
            owner_user_id = old_device.user_id if old_device else None
        if owner_user_id is None:
            raise HTTPException(
                status_code=409,
                detail="Token chưa kích hoạt; không có dữ liệu người dùng để chuyển",
            )
        owner = session.get(User, owner_user_id)

        if body.revoke_existing_devices:
            for device in session.scalars(
                select(Device).where(
                    Device.user_id == owner_user_id,
                    Device.revoked_at.is_(None),
                )
            ):
                _revoke_device_sessions(session, device, now)

        # A replacement supersedes the old code.  The actual durable owner is
        # retained on the user, not on the disposable device/token.
        for old_token in session.scalars(
            select(ActivationToken).where(
                ActivationToken.owner_user_id == owner_user_id,
                ActivationToken.status.in_(["available", "redeemed"]),
            )
        ):
            old_token.status = "revoked"

        code = create_activation_code()
        normalized = normalize_activation_code(code)
        replacement_id = uuid4()
        replacement = ActivationToken(
            id=replacement_id,
            token_hash=sha256(normalized),
            token_hint=normalized[-8:],
            encrypted_code=encrypt_activation_code(
                code, replacement_id, settings.token_export_secret
            ),
            group_id=source.group_id,
            label=source.label,
            note=body.note or f"Cấp lại từ token {source.token_hint}",
            assigned_role=owner.role if owner and owner.role in {"user", "teacher", "student"} else "user",
            exam_limit=(
                owner.exam_limit
                if owner and owner.exam_limit is not None
                else source.exam_limit
            ),
            max_devices=body.max_devices or max(1, min(2, source.max_devices or 1)),
            expires_at=None,
            created_by_user_id=identity["user_id"],
            owner_user_id=owner_user_id,
            parent_token_id=source.id,
        )
        session.add(replacement)
        session.flush()
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="activation.reissued",
                target_type="activation_token",
                target_id=replacement.id,
                detail={
                    "source_token_id": source.id,
                    "owner_user_id": owner_user_id,
                    "revoke_existing_devices": body.revoke_existing_devices,
                    "max_devices": replacement.max_devices,
                },
            )
        )
        return {
            "id": replacement.id,
            "code": code,
            "owner_user_id": owner_user_id,
            "shown_once": True,
            "reexportable": True,
            "data_preserved": True,
        }


@router.post("/admin/users/{user_id}/reissue-token")
def reissue_token_for_user(
    user_id: str, body: UserTokenReissueRequest, request: Request
) -> dict[str, Any]:
    """Move an activated user to a new device without moving their data."""
    identity = require_admin(request)
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None or user.role == "admin":
            raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")

        latest_token = session.scalar(
            select(ActivationToken)
            .where(ActivationToken.owner_user_id == user.id)
            .order_by(ActivationToken.created_at.desc())
            .limit(1)
        )

        # All old/redeemed/unused codes for this owner are invalid after the
        # move. Their exams/attempts remain owned by the same user record.
        for token in session.scalars(
            select(ActivationToken).where(
                ActivationToken.owner_user_id == user.id,
                ActivationToken.status.in_(["available", "redeemed"]),
            )
        ):
            token.status = "revoked"
        for device in session.scalars(
            select(Device).where(Device.user_id == user.id, Device.revoked_at.is_(None))
        ):
            _revoke_device_sessions(session, device, now)

        code = create_activation_code()
        normalized = normalize_activation_code(code)
        replacement_id = uuid4()
        replacement = ActivationToken(
            id=replacement_id,
            token_hash=sha256(normalized),
            token_hint=normalized[-8:],
            encrypted_code=encrypt_activation_code(
                code, replacement_id, settings.token_export_secret
            ),
            group_id=latest_token.group_id if latest_token else None,
            label=body.label or user.display_name,
            note=body.note or "Cấp lại theo người dùng để chuyển máy",
            assigned_role=user.role if user.role in {"user", "teacher", "student"} else "user",
            exam_limit=user.exam_limit or 5,
            max_devices=body.max_devices
            or max(
                1,
                min(
                    2,
                    session.scalar(
                        select(func.max(ActivationToken.max_devices)).where(
                            ActivationToken.owner_user_id == user.id,
                            ActivationToken.status.in_(["available", "redeemed"]),
                        )
                    )
                    or 1,
                ),
            ),
            expires_at=None,
            created_by_user_id=identity["user_id"],
            owner_user_id=user.id,
        )
        session.add(replacement)
        session.flush()
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="activation.user_reissued",
                target_type="user",
                target_id=user.id,
                detail={
                    "token_id": replacement.id,
                    "revoke_existing_devices": True,
                    "max_devices": replacement.max_devices,
                },
            )
        )
        return {
            "id": replacement.id,
            "code": code,
            "owner_user_id": user.id,
            "shown_once": True,
            "reexportable": True,
            "data_preserved": True,
        }


@router.post("/admin/tokens/{token_id}/revoke")
def revoke_token(token_id: str, request: Request) -> dict[str, bool]:
    identity = require_admin(request)
    now = utcnow()
    with session_scope() as session:
        token = session.get(ActivationToken, token_id)
        if token is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy token")
        token.status = "revoked"
        devices = session.scalars(
            select(Device).where(
                or_(
                    Device.activation_token_id == token.id,
                    Device.id == token.redeemed_by_device_id,
                ),
                Device.revoked_at.is_(None),
            )
        ).all()
        device_revoked = False
        for device in devices:
            device_revoked = _revoke_device_sessions(session, device, now) or device_revoked
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="activation.revoked",
                target_type="activation_token",
                target_id=token.id,
                detail={
                    "device_revoked": device_revoked,
                    "device_ids": [device.id for device in devices],
                },
            )
        )
    return {"ok": True}


@router.delete("/admin/tokens/{token_id}")
def permanently_delete_token(token_id: str, request: Request) -> dict[str, bool]:
    """Permanently remove a disposable activation code without deleting its user."""
    identity = require_admin(request)
    with session_scope() as session:
        token = session.get(ActivationToken, token_id)
        if token is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy token")
        session.execute(
            update(ActivationToken)
            .where(ActivationToken.parent_token_id == token.id)
            .values(parent_token_id=None)
        )
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="activation.permanently_deleted",
                target_type="activation_token",
                target_id=token.id,
                detail={"hint": token.token_hint, "owner_user_id": token.owner_user_id},
            )
        )
        session.delete(token)
        logger.info(
            "ACTIVATION_TOKEN_PERMANENTLY_DELETED token_id=%s actor_user_id=%s",
            token_id,
            identity["user_id"],
        )
    return {"ok": True}


@router.get("/admin/devices")
def list_devices(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    require_admin(request)
    with session_scope() as session:
        total = int(session.scalar(select(func.count(Device.id))) or 0)
        rows = session.execute(
            select(Device, User).join(User, User.id == Device.user_id)
            .order_by(Device.activated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {
            "items": [
                {
                    "id": device.id,
                    "name": device.name,
                    "platform": device.platform,
                    "user": user.display_name,
                    "activated_at": device.activated_at,
                    "last_seen_at": device.last_seen_at,
                    "revoked_at": device.revoked_at,
                }
                for device, user in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }


@router.post("/admin/devices/{device_id}/revoke")
def revoke_device(device_id: str, request: Request) -> dict[str, bool]:
    identity = require_admin(request)
    now = utcnow()
    with session_scope() as session:
        device = session.get(Device, device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy thiết bị")
        _revoke_device_sessions(session, device, now)
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="device.revoked",
                target_type="device",
                target_id=device.id,
            )
        )
    return {"ok": True}


# --- Admin password and User CRUD ---

@router.post("/admin/password")
def change_admin_password(
    body: AdminPasswordChangeRequest, request: Request
) -> dict[str, Any]:
    """Change the current admin password and revoke other admin sessions."""
    identity = require_admin(request)
    if body.new_password != body.new_password_confirmation:
        raise HTTPException(status_code=422, detail="Mật khẩu mới không khớp")
    if body.current_password == body.new_password:
        raise HTTPException(
            status_code=422, detail="Mật khẩu mới phải khác mật khẩu hiện tại"
        )

    now = utcnow()
    revoked_sessions = 0
    with session_scope() as session:
        user = session.get(User, identity["user_id"])
        if (
            user is None
            or user.role != "admin"
            or not user.password_hash
            or not verify_password(user.password_hash, body.current_password)
        ):
            raise HTTPException(status_code=400, detail="Mật khẩu hiện tại không đúng")

        user.password_hash = hash_password(body.new_password)
        for device in session.scalars(
            select(Device).where(
                Device.user_id == user.id,
                Device.id != identity["device_id"],
                Device.revoked_at.is_(None),
            )
        ):
            if _revoke_device_sessions(session, device, now):
                revoked_sessions += 1
        session.add(
            AuditLog(
                actor_user_id=user.id,
                action="auth.password_changed",
                target_type="user",
                target_id=user.id,
                detail={"revoked_other_sessions": revoked_sessions},
            )
        )
        identity_cache.invalidate_user(user.id)

    return {"ok": True, "revoked_other_sessions": revoked_sessions}


@router.post("/admin/users/{user_id}/password")
def reset_user_password(
    user_id: str, body: AdminUserPasswordResetRequest, request: Request
) -> dict[str, Any]:
    """Set a non-admin user's password and revoke all of their sessions."""
    identity = require_admin(request)
    if body.new_password != body.new_password_confirmation:
        raise HTTPException(status_code=422, detail="Mật khẩu mới không khớp")

    now = utcnow()
    revoked_sessions = 0
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None or user.role == "admin":
            raise HTTPException(status_code=404, detail="Không tìm thấy học viên")

        user.password_hash = hash_password(body.new_password)
        if user.registered_at is None:
            user.registered_at = now
        for device in session.scalars(
            select(Device).where(
                Device.user_id == user.id,
                Device.revoked_at.is_(None),
            )
        ):
            if _revoke_device_sessions(session, device, now):
                revoked_sessions += 1
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="auth.user_password_reset",
                target_type="user",
                target_id=user.id,
                detail={"revoked_sessions": revoked_sessions},
            )
        )
        identity_cache.invalidate_user(user.id)

    return {
        "ok": True,
        "user_id": user_id,
        "revoked_sessions": revoked_sessions,
    }


@router.post("/admin/users")
def create_user(body: UserCreateRequest, request: Request) -> dict[str, Any]:
    identity = require_admin(request)
    with session_scope() as session:
        if body.email:
            existing = session.scalar(select(User).where(User.email == body.email.lower()))
            if existing:
                raise HTTPException(status_code=409, detail="Email đã được sử dụng")
        user = User(
            display_name=body.display_name.strip(),
            email=body.email.lower().strip() if body.email else None,
            role=body.role if body.role in {"admin", "teacher", "student", "user"} else "user",
            status=body.status if body.status in {"active", "disabled"} else "active",
            exam_limit=(
                None if body.role == "admin" else 0 if body.role == "student" else body.exam_limit
            ),
        )
        session.add(user)
        session.flush()
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="user.created",
                target_type="user",
                target_id=user.id,
            )
        )
        return {
            "id": user.id,
            "display_name": user.display_name,
            "email": user.email,
            "role": user.role,
            "status": user.status,
            "exam_limit": user.exam_limit,
        }


@router.patch("/admin/users/{user_id}")
def update_user(user_id: str, body: UserUpdateRequest, request: Request) -> dict[str, Any]:
    identity = require_admin(request)
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
        if body.display_name is not None:
            user.display_name = body.display_name.strip()
        if body.email is not None:
            email_clean = body.email.lower().strip() if body.email.strip() else None
            if email_clean and email_clean != user.email:
                existing = session.scalar(select(User).where(User.email == email_clean))
                if existing:
                    raise HTTPException(status_code=409, detail="Email đã được sử dụng")
            user.email = email_clean
        if (
            user.role == "teacher"
            and body.role is not None
            and body.role != "teacher"
            and session.scalar(
                select(func.count(Classroom.id)).where(
                    Classroom.owner_teacher_id == user.id
                )
            )
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Giáo viên đang sở hữu lớp học; hãy vô hiệu hóa thay vì "
                    "thay đổi vai trò"
                ),
            )
        if body.role is not None and body.role in {"admin", "teacher", "student", "user"}:
            user.role = body.role
            if body.role == "student":
                user.exam_limit = 0
                session.execute(
                    update(ActivationToken)
                    .where(ActivationToken.owner_user_id == user.id)
                    .values(exam_limit=0, assigned_role="student")
                )
        if body.status is not None and body.status in {"active", "disabled"}:
            user.status = body.status
            if body.status == "disabled":
                # Revoke user devices
                for device in session.scalars(select(Device).where(Device.user_id == user.id, Device.revoked_at.is_(None))):
                    _revoke_device_sessions(session, device, utcnow())
        if body.device_limit is not None:
            active_tokens = session.scalars(
                select(ActivationToken).where(
                    ActivationToken.owner_user_id == user.id,
                    ActivationToken.status.in_(["available", "redeemed"]),
                )
            ).all()
            if not active_tokens:
                raise HTTPException(
                    status_code=409,
                    detail="Người dùng chưa có Key kích hoạt đang hoạt động",
                )
            active_device_count = session.scalar(
                select(func.count(Device.id)).where(
                    Device.user_id == user.id,
                    Device.revoked_at.is_(None),
                )
            ) or 0
            if body.device_limit < active_device_count:
                raise HTTPException(
                    status_code=409,
                    detail="Không thể giảm giới hạn thấp hơn số thiết bị đang hoạt động",
                )
            for token in active_tokens:
                token.max_devices = body.device_limit
        if body.exam_limit is not None and user.role not in {"admin", "student"}:
            user.exam_limit = body.exam_limit
            session.execute(
                update(ActivationToken)
                .where(ActivationToken.owner_user_id == user.id)
                .values(exam_limit=body.exam_limit)
            )
        session.flush()
        identity_cache.invalidate_user(user.id)
        return {
            "id": user.id,
            "display_name": user.display_name,
            "email": user.email,
            "role": user.role,
            "status": user.status,
            "exam_limit": user.exam_limit,
            "device_limit": max(
                1,
                min(
                    2,
                    session.scalar(
                        select(func.max(ActivationToken.max_devices)).where(
                            ActivationToken.owner_user_id == user.id,
                            ActivationToken.status.in_(["available", "redeemed"]),
                        )
                    )
                    or 1,
                ),
            ),
        }


@router.delete("/admin/users/{user_id}")
def delete_user(user_id: str, request: Request) -> dict[str, Any]:
    identity = require_admin(request)
    if identity["user_id"] == user_id:
        raise HTTPException(status_code=400, detail="Không thể tự xóa tài khoản đang đăng nhập")
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
        if user.role == "admin":
            raise HTTPException(status_code=400, detail="Không thể xóa tài khoản quản trị")
        object_refs = set(session.execute(
            select(Asset.bucket, Asset.object_key)
            .join(Exam, Exam.id == Asset.exam_id)
            .where(Exam.owner_user_id == user.id)
        ).all())
        exam_ids = list(
            session.scalars(select(Exam.id).where(Exam.owner_user_id == user.id))
        )
        job_ids = list(
            session.scalars(select(Job.id).where(Job.owner_user_id == user.id))
        )
        source_refs = list(
            session.scalars(
                select(Job.source_object_key).where(
                    Job.owner_user_id == user.id,
                    Job.source_object_key.is_not(None),
                )
            )
        )
        answer_refs = list(
            session.scalars(
                select(AnswerKey.source_object_key).where(
                    AnswerKey.exam_id.in_(exam_ids),
                    AnswerKey.source_object_key.is_not(None),
                )
            )
        ) if exam_ids else []
        version_ids = list(
            session.scalars(
                select(ExamVersion.id).where(ExamVersion.owner_teacher_id == user.id)
            )
        )
        if version_ids:
            object_refs.update(
                session.execute(
                    select(ExamVersionAsset.bucket, ExamVersionAsset.object_key).where(
                        ExamVersionAsset.exam_version_id.in_(version_ids)
                    )
                ).all()
            )
        desktop_syncs = session.scalars(
            select(DesktopSync).where(DesktopSync.user_id == user.id)
        ).all()
        for sync in desktop_syncs:
            for item in (sync.uploaded_assets or {}).values():
                if isinstance(item, dict) and item.get("bucket") and item.get("object_key"):
                    object_refs.add((str(item["bucket"]), str(item["object_key"])))

        classroom_ids = list(
            session.scalars(
                select(Classroom.id).where(Classroom.owner_teacher_id == user.id)
            )
        )
        assignment_conditions = []
        if classroom_ids:
            assignment_conditions.append(ClassAssignment.classroom_id.in_(classroom_ids))
        if version_ids:
            assignment_conditions.append(ClassAssignment.exam_version_id.in_(version_ids))
        assignment_ids = list(
            session.scalars(
                select(ClassAssignment.id).where(or_(*assignment_conditions))
            )
        ) if assignment_conditions else []
        member_conditions = [ClassMember.user_id == user.id]
        if classroom_ids:
            member_conditions.append(ClassMember.classroom_id.in_(classroom_ids))
        member_ids = list(
            session.scalars(select(ClassMember.id).where(or_(*member_conditions)))
        )
        attempt_conditions = [Attempt.user_id == user.id]
        if exam_ids:
            attempt_conditions.append(Attempt.exam_id.in_(exam_ids))
        if assignment_ids:
            attempt_conditions.append(Attempt.class_assignment_id.in_(assignment_ids))
        if member_ids:
            attempt_conditions.append(Attempt.class_member_id.in_(member_ids))
        attempt_ids = list(
            session.scalars(select(Attempt.id).where(or_(*attempt_conditions)))
        )
        device_ids = list(
            session.scalars(select(Device.id).where(Device.user_id == user.id))
        )

        if attempt_ids:
            session.execute(
                delete(AntiCheatEvent).where(AntiCheatEvent.attempt_id.in_(attempt_ids))
            )
            session.execute(
                delete(AttemptAnswer).where(AttemptAnswer.attempt_id.in_(attempt_ids))
            )
            session.execute(delete(Attempt).where(Attempt.id.in_(attempt_ids)))
        if assignment_ids:
            session.execute(
                delete(ClassAssignment).where(ClassAssignment.id.in_(assignment_ids))
            )
        if member_ids:
            session.execute(delete(ClassMember).where(ClassMember.id.in_(member_ids)))
        if classroom_ids:
            session.execute(delete(Classroom).where(Classroom.id.in_(classroom_ids)))
        if version_ids:
            session.execute(
                delete(ExamVersionAsset).where(
                    ExamVersionAsset.exam_version_id.in_(version_ids)
                )
            )
            session.execute(delete(ExamVersion).where(ExamVersion.id.in_(version_ids)))
        if exam_ids:
            session.execute(delete(Asset).where(Asset.exam_id.in_(exam_ids)))
            session.execute(delete(AnswerKey).where(AnswerKey.exam_id.in_(exam_ids)))
            session.execute(
                delete(QuestionRecord).where(QuestionRecord.exam_id.in_(exam_ids))
            )
            session.execute(
                delete(StimulusRecord).where(StimulusRecord.exam_id.in_(exam_ids))
            )
            session.execute(delete(Exam).where(Exam.id.in_(exam_ids)))
        session.execute(delete(DesktopSync).where(DesktopSync.user_id == user.id))
        if device_ids:
            session.execute(
                delete(RefreshToken).where(RefreshToken.device_id.in_(device_ids))
            )
        session.execute(
            delete(ActivationToken).where(ActivationToken.owner_user_id == user.id)
        )
        session.execute(delete(Device).where(Device.user_id == user.id))
        if job_ids:
            session.execute(delete(Job).where(Job.id.in_(job_ids)))
        session.execute(
            update(AuditLog)
            .where(AuditLog.actor_user_id == user.id)
            .values(actor_user_id=None)
        )
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="user.permanently_deleted",
                target_type="user",
                target_id=user_id,
                detail={
                    "display_name": user.display_name,
                    "email": user.email,
                    "role": user.role,
                    "exam_count": len(exam_ids),
                    "classroom_count": len(classroom_ids),
                    "version_count": len(version_ids),
                },
            )
        )
        session.delete(user)
        logger.info(
            "USER_PERMANENTLY_DELETED user_id=%s actor_user_id=%s",
            user_id,
            identity["user_id"],
        )
    identity_cache.invalidate_user(user_id)
    storage_deleted = 0
    storage_failures: list[str] = []

    def storage_key(value: str) -> str:
        clean = value.strip().split("?", 1)[0].lstrip("/")
        parts = clean.split("/")
        if len(parts) >= 4 and parts[:2] == ["api", "extractions"]:
            return "/".join(["jobs", *parts[2:]])
        return clean

    if storage is not None:
        for bucket, object_key in object_refs:
            if not object_key:
                continue
            try:
                storage.remove_object(bucket, storage_key(object_key))
                storage_deleted += 1
            except Exception:
                storage_failures.append(f"{bucket}/{object_key}")
                logger.warning(
                    "Không thể xóa object của người dùng %s: %s/%s",
                    user_id,
                    bucket,
                    object_key,
                    exc_info=True,
                )
        for object_key in source_refs:
            try:
                storage.remove_object(
                    settings.minio_bucket_sources, storage_key(object_key)
                )
                storage_deleted += 1
            except Exception:
                storage_failures.append(
                    f"{settings.minio_bucket_sources}/{object_key}"
                )
                logger.warning(
                    "Không thể xóa source object của người dùng %s: %s",
                    user_id,
                    object_key,
                    exc_info=True,
                )
        for object_key in answer_refs:
            try:
                storage.remove_object(
                    settings.minio_bucket_answers, storage_key(object_key)
                )
                storage_deleted += 1
            except Exception:
                storage_failures.append(
                    f"{settings.minio_bucket_answers}/{object_key}"
                )
                logger.warning(
                    "Không thể xóa ảnh đáp án của người dùng %s: %s",
                    user_id,
                    object_key,
                    exc_info=True,
                )
        for job_id in job_ids:
            for bucket in {
                settings.minio_bucket_sources,
                settings.minio_bucket_assets,
                settings.minio_bucket_audio,
            }:
                try:
                    storage.remove_prefix(bucket, f"jobs/{job_id}/")
                except Exception:
                    storage_failures.append(f"{bucket}/jobs/{job_id}/")
                    logger.warning(
                        "Không thể dọn prefix job của người dùng %s: %s/jobs/%s/",
                        user_id,
                        bucket,
                        job_id,
                        exc_info=True,
                    )
        for bucket in {settings.minio_bucket_assets, settings.minio_bucket_audio}:
            try:
                storage.remove_prefix(bucket, f"desktop/{user_id}/")
            except Exception:
                storage_failures.append(f"{bucket}/desktop/{user_id}/")
                logger.warning(
                    "Không thể dọn prefix desktop của người dùng %s trong %s",
                    user_id,
                    bucket,
                    exc_info=True,
                )
        for version_id in version_ids:
            for bucket in {settings.minio_bucket_assets, settings.minio_bucket_audio}:
                try:
                    storage.remove_prefix(bucket, f"classroom-versions/{version_id}/")
                except Exception:
                    storage_failures.append(
                        f"{bucket}/classroom-versions/{version_id}/"
                    )
                    logger.warning(
                        "Không thể dọn snapshot lớp %s trong %s",
                        version_id,
                        bucket,
                        exc_info=True,
                    )
    return {
        "ok": True,
        "deleted_exams": len(exam_ids),
        "deleted_classrooms": len(classroom_ids),
        "deleted_versions": len(version_ids),
        "storage_objects_deleted": storage_deleted,
        "storage_cleanup_complete": not storage_failures,
    }


# --- Policies & Tags ---

@router.get("/policies/{policy_key}")
def get_policy(policy_key: str) -> dict[str, str]:
    if policy_key not in POLICY_KEYS:
        raise HTTPException(status_code=404, detail="Chính sách không tồn tại")
    with session_scope() as session:
        policy = session.get(SitePolicy, policy_key)
        return _policy_payload(policy_key, policy)


@router.put("/policies/{policy_key}")
def update_policy(policy_key: str, body: PolicyUpdateRequest, request: Request) -> dict[str, str]:
    require_admin(request)
    if policy_key not in POLICY_KEYS:
        raise HTTPException(status_code=404, detail="Chính sách không tồn tại")
    content = body.content.strip()
    content_format = body.content_format
    if content_format == "html":
        content = _render_policy_html(content, content_format)
    if not content:
        raise HTTPException(status_code=422, detail="Nội dung chính sách không hợp lệ")
    with session_scope() as session:
        policy = session.get(SitePolicy, policy_key)
        if policy is None:
            policy = SitePolicy(
                key=policy_key,
                title=body.title.strip(),
                content=content,
                content_format=content_format,
            )
            session.add(policy)
        else:
            policy.title = body.title.strip()
            policy.content = content
            policy.content_format = content_format
            policy.updated_at = utcnow()
        session.flush()
        return _policy_payload(policy_key, policy)


@router.get("/tags")
def list_tags(request: Request) -> dict[str, list[str]]:
    identity = current_identity(request)
    from models import ExamTag
    with session_scope() as session:
        shared = session.scalars(select(ExamTag.name)).all()
        personal = session.scalars(
            select(Exam.category)
            .where(
                Exam.owner_user_id == identity["user_id"],
                Exam.deleted_at.is_(None),
                Exam.category != "",
            )
            .distinct()
        ).all()
        tags = sorted(
            {str(value).strip() for value in [*shared, *personal] if str(value).strip()},
            key=str.casefold,
        )
        return {"items": tags}


@router.post("/tags")
def create_tag(body: TagCreateRequest, request: Request) -> dict[str, str]:
    identity = current_identity(request)
    name_clean = " ".join(body.name.split())
    if not name_clean:
        raise HTTPException(status_code=400, detail="Tên Tag không được rỗng")
    # Personal libraries store their lightweight Tag in Exam.category. They
    # may select it immediately and finalize without mutating the shared
    # Teacher taxonomy.
    if identity["role"] not in {"teacher", "admin"}:
        return {"name": name_clean}
    from models import ExamTag
    name_key = normalized_name_key(name_clean)
    try:
        with session_scope() as session:
            existing = session.scalar(select(ExamTag).where(ExamTag.name_key == name_key))
            if existing is None:
                tag = ExamTag(name=name_clean, name_key=name_key)
                session.add(tag)
                session.flush()
            else:
                name_clean = existing.name
            return {"name": name_clean}
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Tag đã tồn tại") from exc


class PublicStartRequest(BaseModel):
    student_name: str = Field(min_length=1, max_length=160)
    phone: str | None = Field(default="", max_length=40)
    email: str | None = Field(default="", max_length=320)


class PublicSubmitRequest(BaseModel):
    submission_token: str = Field(min_length=32, max_length=2048)
    answers: dict[str, str] = Field(default_factory=dict, max_length=200)
    time_spent_seconds: int = Field(default=0, ge=0, le=86_400)

    @field_validator("answers")
    @classmethod
    def validate_answers(cls, answers: dict[str, str]) -> dict[str, str]:
        for number, answer in answers.items():
            if not str(number).isdigit() or int(number) < 1 or int(number) > 1000:
                raise ValueError("Số câu trả lời không hợp lệ")
            if len(answer) > 8:
                raise ValueError("Đáp án vượt quá độ dài cho phép")
        return answers


@router.post("/exams/{exam_id}/public-share")
def create_or_get_public_share(exam_id: str, request: Request) -> dict[str, Any]:
    import uuid
    identity = current_identity(request)
    from models import PublicExamShare
    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        if exam is None:
            exam = session.scalar(select(Exam).where(Exam.client_exam_id == exam_id))
        if exam is None or exam.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Không tìm thấy đề thi")
        if not _can_manage_exam(exam, identity):
            raise HTTPException(status_code=403, detail="Không có quyền chia sẻ đề thi này")
        share = session.scalar(select(PublicExamShare).where(PublicExamShare.exam_id == exam.id))
        if share is None:
            code_prefix = "mini-" + uuid.uuid4().hex[:8]
            share = PublicExamShare(
                exam_id=exam.id,
                share_code=code_prefix,
                created_by_user_id=identity["user_id"],
                is_active=True,
            )
            session.add(share)
            session.flush()
        return {
            "share_code": share.share_code,
            "public_url": f"/public-test/{share.share_code}",
            "exam_id": exam.id,
            "title": exam.title,
        }


@router.get("/exams/{exam_id}/public-submissions")
def get_public_submissions(
    exam_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    identity = current_identity(request)
    from models import PublicExamSubmission
    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        if exam is None:
            exam = session.scalar(select(Exam).where(Exam.client_exam_id == exam_id))
        if exam is None or exam.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Không tìm thấy đề thi")
        if not _can_manage_exam(exam, identity):
            raise HTTPException(status_code=403, detail="Không có quyền xem kết quả đề thi này")
        base_query = select(PublicExamSubmission).where(
            PublicExamSubmission.exam_id == exam.id
        )
        total = session.scalar(
            select(func.count()).select_from(base_query.subquery())
        ) or 0
        submissions = session.scalars(
            base_query
            .order_by(PublicExamSubmission.started_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {
            "exam_id": exam.id,
            "title": exam.title,
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": page * page_size < total,
            "items": [
                {
                    "id": sub.id,
                    "student_name": sub.student_name,
                    "phone": sub.phone or "",
                    "email": sub.email or "",
                    "status": sub.status,
                    "started_at": sub.started_at.isoformat() if sub.started_at else None,
                    "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
                    "time_spent_seconds": sub.time_spent_seconds or 0,
                    "total_correct": sub.total_correct or 0,
                    "question_count": sub.question_count or 0,
                    "score_toeic": sub.score_toeic or 0,
                    "listening_score": sub.listening_score or 0,
                    "reading_score": sub.reading_score or 0,
                    "part_breakdown": sub.part_breakdown or {},
                    "answers": sub.answers or {},
                }
                for sub in submissions
            ],
        }


@router.delete("/public-submissions/{submission_id}")
def delete_public_submission(submission_id: str, request: Request) -> dict[str, bool]:
    identity = current_identity(request)
    from models import PublicExamSubmission
    with session_scope() as session:
        sub = session.get(PublicExamSubmission, submission_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài làm")
        exam = session.get(Exam, sub.exam_id)
        if exam is None or not _can_manage_exam(exam, identity):
            raise HTTPException(status_code=403, detail="Không có quyền xóa kết quả này")
        session.delete(sub)
        session.flush()
        return {"ok": True}


@router.get("/public-tests/{share_code}")
def get_public_test_details(share_code: str) -> dict[str, Any]:
    from models import PublicExamShare
    with session_scope() as session:
        share = session.scalar(
            select(PublicExamShare).where(
                PublicExamShare.share_code == share_code,
                PublicExamShare.is_active == True,
            )
        )
        if share is None:
            raise HTTPException(
                status_code=404, detail="Link bài thi không tồn tại hoặc đã bị tắt"
            )
        exam = session.get(Exam, share.exam_id)
        if exam is None or exam.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Đề thi đã bị xóa")
        version = (
            session.get(ExamVersion, exam.current_version_id)
            if exam.current_version_id
            else None
        )
        payload = _public_version_payload(session, exam, version)
        return {
            "share_code": share_code,
            "title": exam.title,
            "exam": payload,
        }


@router.post("/public-tests/{share_code}/start")
def start_public_test(share_code: str, body: PublicStartRequest) -> dict[str, Any]:
    from models import PublicExamShare, PublicExamSubmission
    student_name = body.student_name.strip()
    if not student_name:
        raise HTTPException(status_code=422, detail="Vui lòng nhập họ và tên")
    with session_scope() as session:
        share = session.scalar(
            select(PublicExamShare).where(
                PublicExamShare.share_code == share_code,
                PublicExamShare.is_active == True,
            )
        )
        if share is None:
            raise HTTPException(
                status_code=404, detail="Link bài thi không tồn tại hoặc đã bị tắt"
            )
        exam = session.get(Exam, share.exam_id)
        if exam is None or exam.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Đề thi đã bị xóa")

        version = (
            session.get(ExamVersion, exam.current_version_id)
            if exam.current_version_id
            else None
        )
        if version is None:
            raise HTTPException(status_code=409, detail="Đề chưa có version khả dụng")
        submission = PublicExamSubmission(
            share_id=share.id,
            exam_id=exam.id,
            exam_version_id=version.id,
            student_name=student_name,
            phone=body.phone.strip() if body.phone else "",
            email=body.email.strip() if body.email else "",
            status="in_progress",
            started_at=utcnow(),
            question_count=version.question_count,
        )
        session.add(submission)
        session.flush()

        payload = _public_version_payload(session, exam, version)
        return {
            "submission_id": submission.id,
            "submission_token": _public_submission_token(submission.id, share.id),
            "student_name": student_name,
            "exam": payload,
        }


@router.post("/public-tests/submissions/{submission_id}/submit")
def submit_public_test(
    submission_id: str, body: PublicSubmitRequest
) -> dict[str, Any]:
    from models import PublicExamSubmission
    token_payload = _verify_public_submission_token(body.submission_token, submission_id)
    with session_scope() as session:
        sub = session.scalar(
            select(PublicExamSubmission)
            .where(PublicExamSubmission.id == submission_id)
            .with_for_update()
        )
        if sub is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài làm")
        if token_payload.get("share_id") != sub.share_id:
            raise HTTPException(status_code=401, detail="Phiên public test không hợp lệ")
        if sub.status == "submitted":
            return {
                "submission_id": sub.id,
                "status": sub.status,
                "total_correct": sub.total_correct,
                "question_count": sub.question_count,
                "score_toeic": sub.score_toeic,
                "listening_score": sub.listening_score,
                "reading_score": sub.reading_score,
                "part_breakdown": sub.part_breakdown,
                "answers": sub.answers,
            }

        exam = session.get(Exam, sub.exam_id)
        if exam is None:
            raise HTTPException(status_code=404, detail="Đề thi không tồn tại")

        version = (
            session.get(ExamVersion, sub.exam_version_id)
            if sub.exam_version_id
            else None
        )
        if version is None:
            raise HTTPException(
                status_code=409, detail="Version của bài làm không còn tồn tại"
            )
        payload = version.payload or {}
        projection = {
            row.question_number: row.correct
            for row in session.scalars(
                select(ExamVersionQuestion).where(
                    ExamVersionQuestion.exam_version_id == version.id
                )
            )
        }
        questions = payload.get("questions") or []

        total_correct = 0
        part_stats: dict[str, dict[str, int]] = {}
        detail_answers: dict[str, dict[str, Any]] = {}

        for q in questions:
            q_num_str = str(q.get("number"))
            try:
                question_number = int(q.get("number"))
            except (TypeError, ValueError):
                continue
            correct_ans = (projection.get(question_number) or "").strip().upper()
            part = str(q.get("part") or "Part 1")

            if part not in part_stats:
                part_stats[part] = {"correct": 0, "total": 0}
            part_stats[part]["total"] += 1

            selected_ans = (body.answers.get(q_num_str) or "").strip().upper()
            is_correct = False
            if correct_ans and selected_ans == correct_ans:
                is_correct = True
                total_correct += 1
                part_stats[part]["correct"] += 1

            detail_answers[q_num_str] = {
                "selected": selected_ans,
                "correct": correct_ans,
                "is_correct": is_correct,
            }

        q_count = len(projection) or version.question_count or 1
        score_percent = (total_correct / q_count) * 990 if q_count > 0 else 0
        estimated_toeic = int(round(score_percent / 5.0) * 5)

        sub.status = "submitted"
        sub.submitted_at = utcnow()
        sub.time_spent_seconds = body.time_spent_seconds
        sub.total_correct = total_correct
        sub.question_count = q_count
        sub.score_toeic = min(990, max(10, estimated_toeic))
        sub.part_breakdown = part_stats
        sub.answers = detail_answers
        session.flush()

        return {
            "submission_id": sub.id,
            "status": sub.status,
            "total_correct": total_correct,
            "question_count": q_count,
            "score_toeic": sub.score_toeic,
            "part_breakdown": part_stats,
            "answers": detail_answers,
        }
