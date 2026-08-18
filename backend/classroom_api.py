"""Teacher classrooms, anonymous class sessions and monitored attempts."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import mimetypes
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import quote

import jwt
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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
from auth_service import current_identity, require_roles, require_teacher, sha256
from config import settings
from database import session_scope
from manifest_cache import manifest_cache
from media_auth_cache import media_auth_cache
from models import (
    AntiCheatEvent,
    Asset,
    Attempt,
    AttemptAnswer,
    AuditLog,
    ClassAssignment,
    ClassMember,
    Classroom,
    ClassroomCoTeacher,
    Exam,
    ExamVersion,
    ExamVersionAsset,
    ExamVersionQuestion,
    User,
    utcnow,
    uuid4,
)
from object_storage import storage
from presence_store import PRESENCE_TTL_SECONDS, presence_store
from pagination import decode_submitted_cursor, encode_submitted_cursor
from toeic_score import scores as toeic_scores


router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)
CLASS_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CLASS_EVENT_TYPES = {
    "visibility_hidden",
    "visibility_visible",
    "window_blur",
    "window_focus",
    "fullscreen_exit",
    "fullscreen_enter",
    "fullscreen_unsupported",
    "offline",
    "online",
    "reload",
    "unload",
    "copy",
    "paste",
    "context_menu",
}
STUDY_PUBLICATION_KINDS = {"study_resource", "bank_practice", "bank_mock_exam"}


class ClassroomCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    description: str = Field(default="", max_length=4000)


class ClassroomUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    status: Literal["active", "archived"] | None = None


class ClassroomResolveRequest(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class ClassroomJoinRequest(ClassroomResolveRequest):
    full_name: str = Field(min_length=2, max_length=160)
    browser_key: str = Field(min_length=16, max_length=512)


class StudentJoinRequest(ClassroomResolveRequest):
    legacy_session_token: str | None = Field(default=None, max_length=2048)


class AssignmentCreateRequest(BaseModel):
    exam_id: str
    title: str | None = Field(default=None, max_length=255)
    mode: Literal["exam", "practice"] = "exam"
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    duration_seconds: int | None = Field(default=None, ge=60, le=300 * 60)
    attempt_limit: int | None = Field(default=1, ge=1, le=100)
    score_release: Literal["immediate", "after_close", "manual"] = "immediate"
    answer_release: Literal["immediate", "after_close", "manual", "never"] = "manual"
    anti_cheat_enabled: bool = True
    listening_navigation_locked: bool = True
    publish: bool = True


class ClassPublicationRequest(BaseModel):
    classroom_ids: list[str] = Field(min_length=1, max_length=100)


class TagClassPublicationRequest(ClassPublicationRequest):
    tag: str = Field(min_length=1, max_length=120)


class StudentAttemptStartRequest(BaseModel):
    launch_mode: Literal["practice", "mock_exam"] | None = None
    part_numbers: list[int] | None = Field(default=None, min_length=1, max_length=7)
    duration_seconds: int | None = Field(default=None, ge=60, le=300 * 60)


class AssignmentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    mode: Literal["exam", "practice"] | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    duration_seconds: int | None = Field(default=None, ge=60, le=300 * 60)
    attempt_limit: int | None = Field(default=None, ge=1, le=100)
    score_release: Literal["immediate", "after_close", "manual"] | None = None
    answer_release: Literal["immediate", "after_close", "manual", "never"] | None = None
    anti_cheat_enabled: bool | None = None
    listening_navigation_locked: bool | None = None


class AssignmentReopenRequest(BaseModel):
    closes_at: datetime | None = None
    additional_attempts: int = Field(default=1, ge=1, le=100)


class AttemptAnswersRequest(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict, max_length=200)
    time_left_seconds: int = Field(ge=0)
    client_revision: int | None = Field(default=None, ge=0)


class HeartbeatRequest(BaseModel):
    answered_count: int = Field(default=0, ge=0, le=1000)
    current_question_number: int | None = None
    time_left_seconds: int = Field(ge=0)
    is_fullscreen: bool | None = None
    visibility_state: Literal["visible", "hidden"] | None = None


class AntiCheatEventRequest(BaseModel):
    client_event_id: str = Field(min_length=4, max_length=80)
    event_type: str = Field(min_length=2, max_length=40)
    occurred_at: datetime | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class AntiCheatEventsRequest(BaseModel):
    events: list[AntiCheatEventRequest] = Field(min_length=1, max_length=50)


class MemberStatusRequest(BaseModel):
    status: Literal["active", "removed"]


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _presence_payload(
    attempt: Attempt,
    *,
    answered_count: int,
    current_question_number: int | None,
    time_left_seconds: int,
    is_fullscreen: bool | None,
    visibility_state: str | None,
) -> dict[str, Any]:
    return {
        "attempt_id": attempt.id,
        "answered_count": answered_count,
        "current_question_number": current_question_number,
        "time_left_seconds": time_left_seconds,
        "is_fullscreen": is_fullscreen,
        "visibility_state": visibility_state,
        "last_heartbeat_at": utcnow().isoformat(),
    }


def _normalize_code(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def _new_join_code() -> str:
    return "".join(secrets.choice(CLASS_CODE_ALPHABET) for _ in range(8))


def _unique_join_code(session: Any) -> str:
    for _ in range(20):
        code = _new_join_code()
        if session.scalar(select(Classroom.id).where(Classroom.join_code == code)) is None:
            return code
    raise HTTPException(status_code=503, detail="Không thể sinh mã lớp lúc này")


def _clean_name(value: str) -> str:
    clean = " ".join(value.split())
    if len(clean) < 2:
        raise HTTPException(status_code=422, detail="Vui lòng nhập đầy đủ họ và tên")
    return clean


def _rate_limit_join(
    request: Request,
    code: str,
    browser_key: str | None = None,
    *,
    trusted_subject: str | None = None,
) -> None:
    """Best-effort distributed limiter; availability never depends on Redis.

    Authenticated students are keyed by their durable user id so a classroom
    behind one school/NAT address does not lock out the eleventh legitimate
    student. Guest flows retain the IP scope because their browser key is
    attacker-controlled and can be rotated cheaply.
    """
    identity = getattr(request.state, "identity", None)
    if identity and str(identity.get("role") or "").casefold() == "teacher":
        return
    try:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.15,
            socket_timeout=0.15,
        )
        address = request.client.host if request.client else "unknown"
        scopes = (
            [f"user:{sha256(trusted_subject)}"]
            if trusted_subject
            else [f"ip:{address}"]
        )
        if browser_key and not trusted_subject:
            scopes.append(f"browser:{sha256(browser_key)}")
        window = int(datetime.now().timestamp() // 60)
        for scope in scopes:
            digest = hashlib.sha256(f"{scope}:{code}".encode()).hexdigest()[:24]
            key = f"classroom:join:{digest}:{window}"
            count = client.incr(key)
            if count == 1:
                client.expire(key, 90)
            if count > 10:
                raise HTTPException(
                    status_code=429,
                    detail="Bạn đã thử mã lớp quá nhiều lần, vui lòng chờ một phút",
                )
    except HTTPException:
        raise
    except Exception:
        logger.debug("CLASSROOM_RATE_LIMIT_REDIS_UNAVAILABLE", exc_info=True)


def _issue_class_session(member: ClassMember) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": member.id,
            "class_id": member.classroom_id,
            "type": "class_session",
            "iat": now,
            "exp": now + timedelta(days=30),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def _class_session(request: Request, session: Any) -> tuple[ClassMember, Classroom]:
    account_attempt_id = getattr(request.state, "student_attempt_id", None)
    if account_attempt_id:
        identity = require_roles(request, "student")
        row = session.execute(
            select(ClassMember, Classroom)
            .join(Attempt, Attempt.class_member_id == ClassMember.id)
            .join(Classroom, Classroom.id == ClassMember.classroom_id)
            .where(
                Attempt.id == account_attempt_id,
                ClassMember.user_id == identity["user_id"],
                ClassMember.status == "active",
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy lượt làm bài")
        member, classroom = row
        now = utcnow()
        last_seen = _aware(member.last_seen_at)
        if (
            last_seen is None
            or now - last_seen
            >= timedelta(seconds=max(10, settings.presence_write_interval_seconds))
        ):
            member.last_seen_at = now
        return member, classroom
    student_assignment_id = getattr(request.state, "student_assignment_id", None)
    student_classroom_id = getattr(request.state, "student_classroom_id", None)
    if student_assignment_id or student_classroom_id:
        identity = require_roles(request, "student")
        query = (
            select(ClassMember, Classroom)
            .join(Classroom, Classroom.id == ClassMember.classroom_id)
            .where(
                ClassMember.user_id == identity["user_id"],
                ClassMember.status == "active",
                Classroom.status == "active",
            )
        )
        if student_assignment_id:
            query = query.join(
                ClassAssignment,
                ClassAssignment.classroom_id == Classroom.id,
            ).where(
                ClassAssignment.id == student_assignment_id,
                ClassAssignment.status == "published",
            )
        else:
            query = query.where(Classroom.id == student_classroom_id)
        row = session.execute(query).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Bài thi hoặc lớp học không khả dụng")
        return row
    token = request.headers.get("x-classroom-session", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Thiếu phiên lớp học")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Phiên lớp học không hợp lệ") from exc
    if payload.get("type") != "class_session":
        raise HTTPException(status_code=401, detail="Sai loại phiên lớp học")
    row = session.execute(
        select(ClassMember, Classroom)
        .join(Classroom, Classroom.id == ClassMember.classroom_id)
        .where(
            ClassMember.id == payload.get("sub"),
            Classroom.id == payload.get("class_id"),
            ClassMember.status == "active",
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=403, detail="Thành viên không còn trong lớp")
    member, classroom = row
    now = utcnow()
    last_seen = _aware(member.last_seen_at)
    if (
        last_seen is None
        or now - last_seen
        >= timedelta(seconds=max(10, settings.presence_write_interval_seconds))
    ):
        member.last_seen_at = now
    return member, classroom


def _attempt_session_context(
    request: Request,
    session: Any,
    attempt_id: str,
    *,
    lock: bool = False,
) -> tuple[ClassMember, Classroom, Attempt, ClassAssignment, ExamVersion]:
    """Authorize and load the complete hot attempt context in one DB query."""

    account_attempt_id = getattr(request.state, "student_attempt_id", None)
    filters: list[Any] = [
        Attempt.id == attempt_id,
        ClassMember.status == "active",
        ClassAssignment.classroom_id == ClassMember.classroom_id,
    ]
    if account_attempt_id:
        identity = require_roles(request, "student")
        if account_attempt_id != attempt_id:
            raise HTTPException(status_code=403, detail="Sai lượt làm bài")
        filters.append(ClassMember.user_id == identity["user_id"])
    else:
        token = request.headers.get("x-classroom-session", "").strip()
        if not token:
            raise HTTPException(status_code=401, detail="Thiếu phiên lớp học")
        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail="Phiên lớp học không hợp lệ") from exc
        if payload.get("type") != "class_session":
            raise HTTPException(status_code=401, detail="Sai loại phiên lớp học")
        filters.extend(
            (
                ClassMember.id == payload.get("sub"),
                Classroom.id == payload.get("class_id"),
            )
        )
    query = (
        select(ClassMember, Classroom, Attempt, ClassAssignment, ExamVersion)
        .join(Attempt, Attempt.class_member_id == ClassMember.id)
        .join(ClassAssignment, ClassAssignment.id == Attempt.class_assignment_id)
        .join(Classroom, Classroom.id == ClassMember.classroom_id)
        .join(ExamVersion, ExamVersion.id == ClassAssignment.exam_version_id)
        .where(*filters)
    )
    if lock:
        query = query.with_for_update(of=Attempt)
    row = session.execute(query).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lượt làm bài")
    return row


def _owned_classroom(session: Any, classroom_id: str, teacher_id: str) -> Classroom:
    """Strict owner-only check.  Used for actions reserved for the classroom creator."""
    classroom = session.get(Classroom, classroom_id)
    if classroom is None or classroom.owner_teacher_id != teacher_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học")
    return classroom


def _accessible_classroom(session: Any, classroom_id: str, teacher_id: str) -> Classroom:
    """Owner **or** active co-teacher.  Used for most management operations."""
    classroom = session.get(Classroom, classroom_id)
    if classroom is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học")
    if classroom.owner_teacher_id == teacher_id:
        return classroom
    co = session.scalar(
        select(ClassroomCoTeacher).where(
            ClassroomCoTeacher.classroom_id == classroom_id,
            ClassroomCoTeacher.teacher_user_id == teacher_id,
            ClassroomCoTeacher.status == "active",
        )
    )
    if co is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học")
    return classroom


def _teacher_role_in_classroom(
    session: Any, classroom: Classroom, teacher_id: str
) -> str:
    """Return 'owner' or 'co_teacher' for display purposes."""
    if classroom.owner_teacher_id == teacher_id:
        return "owner"
    return "co_teacher"


def _assignment_payload(assignment: ClassAssignment, version: ExamVersion) -> dict[str, Any]:
    study_resource = _is_study_resource(assignment)
    category = str((version.payload or {}).get("category") or "").strip()
    return {
        "id": assignment.id,
        "title": assignment.title,
        "mode": "practice" if study_resource else assignment.mode,
        "kind": "study_resource" if study_resource else "official_exam",
        "status": assignment.status,
        "opens_at": assignment.opens_at,
        "closes_at": assignment.closes_at,
        "duration_seconds": assignment.duration_seconds,
        "attempt_limit": (
            None
            if study_resource or assignment.mode == "practice"
            else assignment.attempt_limit
        ),
        "score_release": assignment.score_release,
        "answer_release": assignment.answer_release,
        "anti_cheat_enabled": False if study_resource else assignment.anti_cheat_enabled,
        "listening_navigation_locked": (
            False if study_resource else assignment.listening_navigation_locked
        ),
        "published_at": assignment.published_at,
        "available_part_numbers": _available_part_numbers(version.payload or {}),
        "tag": category or None,
        "exam": {
            "id": version.source_exam_id,
            "version_id": version.id,
            "title": version.title,
            "exam_type": version.exam_type,
            "question_count": version.question_count,
            "answer_key_count": version.answer_key_count,
            "duration_minutes": version.duration_minutes,
            "category": category or None,
        },
    }


def _classroom_payload(
    session: Any,
    classroom: Classroom,
    *,
    include_code: bool = True,
    member_count: int | None = None,
    assignment_count: int | None = None,
    teacher_id: str | None = None,
) -> dict[str, Any]:
    result = {
        "id": classroom.id,
        "name": classroom.name,
        "description": classroom.description,
        "status": classroom.status,
        "created_at": classroom.created_at,
        "updated_at": classroom.updated_at,
        "member_count": (
            member_count
            if member_count is not None
            else session.scalar(
                select(func.count(ClassMember.id)).where(
                    ClassMember.classroom_id == classroom.id,
                    ClassMember.status == "active",
                )
            )
            or 0
        ),
        "assignment_count": (
            assignment_count
            if assignment_count is not None
            else session.scalar(
                select(func.count(ClassAssignment.id)).where(
                    ClassAssignment.classroom_id == classroom.id
                )
            )
            or 0
        ),
    }
    if include_code:
        result["join_code"] = classroom.join_code
    if teacher_id:
        role = _teacher_role_in_classroom(session, classroom, teacher_id)
        result["role"] = role
        result["is_owner"] = role == "owner"
    return result


def _classroom_counts(
    session: Any, classroom_ids: list[str]
) -> tuple[dict[str, int], dict[str, int]]:
    if not classroom_ids:
        return {}, {}
    member_counts = dict(
        session.execute(
            select(ClassMember.classroom_id, func.count(ClassMember.id))
            .where(
                ClassMember.classroom_id.in_(classroom_ids),
                ClassMember.status == "active",
            )
            .group_by(ClassMember.classroom_id)
        ).all()
    )
    assignment_counts = dict(
        session.execute(
            select(ClassAssignment.classroom_id, func.count(ClassAssignment.id))
            .where(ClassAssignment.classroom_id.in_(classroom_ids))
            .group_by(ClassAssignment.classroom_id)
        ).all()
    )
    return member_counts, assignment_counts


def _normalized_asset_object_key(value: str) -> str:
    """Convert legacy API URLs stored as object keys back to their MinIO key."""
    raw = value.strip().split("?", 1)[0].lstrip("/")
    parts = raw.split("/")
    if (
        len(parts) == 5
        and parts[:2] == ["api", "extractions"]
        and parts[3] in {"assets", "audio"}
    ):
        return f"jobs/{parts[2]}/{parts[3]}/{parts[4]}"
    return raw


def _part_number_for_question(number: int) -> int | None:
    for part_number, start, end in (
        (1, 1, 6),
        (2, 7, 31),
        (3, 32, 70),
        (4, 71, 100),
        (5, 101, 130),
        (6, 131, 146),
        (7, 147, 200),
    ):
        if start <= number <= end:
            return part_number
    return None


def _available_part_numbers(payload: dict[str, Any]) -> list[int]:
    return sorted(
        {
            part
            for question in payload.get("questions", [])
            if (part := _part_number_for_question(int(question.get("number", 0))))
            is not None
        }
    )


def _is_study_resource(assignment: ClassAssignment) -> bool:
    return assignment.publication_kind in STUDY_PUBLICATION_KINDS


def _attempt_payload(version: ExamVersion, attempt: Attempt | None = None) -> dict[str, Any]:
    payload = copy.deepcopy(version.payload or {})
    if attempt and attempt.selected_part_numbers:
        payload = _filtered_publication_payload(
            payload, [int(part) for part in attempt.selected_part_numbers]
        )
    return payload


def _filtered_publication_payload(
    source_payload: dict[str, Any], part_numbers: list[int]
) -> dict[str, Any]:
    selected_parts = set(part_numbers)
    invalid_parts = selected_parts.difference(range(1, 8))
    if invalid_parts:
        raise HTTPException(status_code=422, detail="Part phải nằm trong khoảng 1–7")

    payload = copy.deepcopy(source_payload)
    questions = [
        question
        for question in payload.get("questions", [])
        if _part_number_for_question(int(question.get("number", 0))) in selected_parts
    ]
    if not questions:
        raise HTTPException(
            status_code=422,
            detail="Đề không có câu hỏi thuộc các Part đã chọn",
        )
    missing_answers = [
        int(question.get("number", 0))
        for question in questions
        if not str(question.get("correct") or "").strip()
    ]
    if missing_answers:
        raise HTTPException(
            status_code=422,
            detail="Các Part đã chọn phải có đáp án đầy đủ",
        )

    selected_numbers = {int(question.get("number", 0)) for question in questions}
    referenced_stimuli = {
        str(question.get("stimulus_id"))
        for question in questions
        if question.get("stimulus_id")
    }
    filtered_stimuli: list[dict[str, Any]] = []
    for stimulus in payload.get("stimuli", []):
        question_numbers = [
            int(number)
            for number in stimulus.get("question_numbers", [])
            if int(number) in selected_numbers
        ]
        if str(stimulus.get("id")) in referenced_stimuli or question_numbers:
            stimulus["question_numbers"] = question_numbers
            filtered_stimuli.append(stimulus)

    payload["questions"] = questions
    payload["stimuli"] = filtered_stimuli
    payload["solutions"] = [
        entry
        for entry in payload.get("solutions", [])
        if set(int(number) for number in entry.get("question_numbers", [])).issubset(
            selected_numbers
        )
    ]
    payload["requested_count"] = len(questions)
    payload["returned_count"] = len(questions)
    payload["total"] = len(questions)
    includes_listening = bool(selected_parts.intersection({1, 2, 3, 4}))
    if payload.get("audios"):
        allowed_audio_parts = {f"part_{part}" for part in selected_parts}
        payload["audios"] = [
            audio
            for audio in payload["audios"]
            if audio.get("part") in allowed_audio_parts
            or (includes_listening and audio.get("part") == "full")
            or (1 in selected_parts and audio.get("part") == "directions_part_1")
        ]
    if not includes_listening:
        payload["audio"] = None
    return payload


def _snapshot_exam(
    session: Any,
    exam: Exam,
    teacher_id: str,
    *,
    payload_override: dict[str, Any] | None = None,
    duration_seconds: int | None = None,
) -> ExamVersion:
    payload = copy.deepcopy(payload_override if payload_override is not None else exam.payload or {})
    # Category is part of the immutable classroom snapshot so student filters keep
    # working even if the source exam is later renamed, retagged or deleted.
    payload["category"] = (exam.category or "").strip()
    payload["slug"] = exam.slug
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    existing = session.scalar(
        select(ExamVersion).where(
            ExamVersion.source_exam_id == exam.id,
            ExamVersion.content_hash == content_hash,
        )
    )
    if existing is not None:
        return existing
    questions = payload.get("questions", [])
    latest = session.scalar(
        select(func.max(ExamVersion.version_number)).where(
            ExamVersion.source_exam_id == exam.id
        )
    )
    version = ExamVersion(
        source_exam_id=exam.id,
        owner_teacher_id=teacher_id,
        version_number=(latest or 0) + 1,
        title=exam.title,
        exam_type=exam.exam_type,
        duration_minutes=(
            max(1, (duration_seconds + 59) // 60)
            if duration_seconds is not None
            else exam.duration_minutes
        ),
        question_count=len(questions),
        answer_key_count=sum(
            bool(str(question.get("correct") or "").strip()) for question in questions
        ),
        content_hash=content_hash,
        payload=payload,
    )
    session.add(version)
    session.flush()
    projected: dict[int, dict[str, Any]] = {}
    for question in questions:
        try:
            number = int(question.get("number"))
        except (AttributeError, TypeError, ValueError):
            continue
        if number <= 0 or number in projected:
            continue
        correct = str(question.get("correct") or "").strip().upper()
        projected[number] = {
            "exam_version_id": version.id,
            "question_number": number,
            "part_number": _part_number_for_question(number),
            "correct": correct if correct in {"A", "B", "C", "D"} else None,
        }
    if projected:
        # One executemany statement keeps snapshot cost bounded even for a
        # 200-question Full Test.
        session.execute(ExamVersionQuestion.__table__.insert(), list(projected.values()))
    assets = session.scalars(
        select(Asset)
        .where(Asset.exam_id == exam.id)
        .order_by(Asset.display_order, Asset.created_at)
    ).all()
    unique_assets: dict[tuple[str, str], tuple[Asset, str]] = {}
    for asset in assets:
        source_key = _normalized_asset_object_key(asset.object_key)
        asset_ref = source_key.rsplit("/", 1)[-1] or asset.filename
        unique_assets.setdefault((asset.bucket, asset_ref), (asset, source_key))

    version_assets = [
        {
            "id": uuid4(),
            "exam_version_id": version.id,
            "kind": asset.kind,
            "bucket": asset.bucket,
            # Client extraction keys are immutable and already include the
            # reserved exam/revision. Object verification/materialization is
            # deliberately outside this transaction.
            "object_key": source_key,
            "filename": asset_ref,
            "content_type": asset.content_type,
            "size": asset.size,
            "sha256": asset.sha256,
            "display_order": asset.display_order,
            "created_at": utcnow(),
        }
        for (_, asset_ref), (asset, source_key) in unique_assets.items()
    ]
    if version_assets:
        session.execute(ExamVersionAsset.__table__.insert(), version_assets)
    return version


def _class_asset_url(
    asset_id: str,
    version_id: str,
    assignment: ClassAssignment | None = None,
    member_id: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "type": "class_asset",
            "asset_id": asset_id,
            "version_id": version_id,
            "assignment_id": assignment.id if assignment else "preview",
            "member_id": member_id or "preview",
            "iat": now,
            "exp": now
            + timedelta(minutes=max(15, settings.class_asset_token_minutes)),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    # Keep the URL same-origin.  The browser may be using the configured
    # domain, a LAN IP, or a local desktop gateway; an absolute URL built from
    # PUBLIC_BASE_URL breaks the latter two and prevents the signed proxy from
    # reaching MinIO.  The token still protects the object at the API layer.
    return f"/api/v1/class-assets/{asset_id}?token={token}"


def _exam_for_student(
    session: Any,
    version: ExamVersion,
    reveal_answers: bool = False,
    *,
    assignment: ClassAssignment | None = None,
    member_id: str | None = None,
    attempt: Attempt | None = None,
) -> dict:
    selected_parts = tuple(
        sorted(int(part) for part in (attempt.selected_part_numbers or []))
    ) if attempt else ()
    cache_key = ":".join(
        (
            version.id,
            version.content_hash,
            "-".join(map(str, selected_parts)) or "all",
            "answers" if reveal_answers else "sanitized",
        )
    )

    def build_manifest() -> dict[str, Any]:
        manifest = _attempt_payload(version, attempt)
        manifest["exam_id"] = version.source_exam_id
        if not manifest.get("slug"):
            source_exam = session.get(Exam, version.source_exam_id)
            if source_exam is not None:
                manifest["slug"] = source_exam.slug
        manifest["title"] = version.title
        manifest["exam_type"] = version.exam_type
        manifest.pop("answer_key", None)
        if not reveal_answers:
            manifest.pop("solutions", None)
            for question in manifest.get("questions") or []:
                # Keep the established response shape while never exposing the
                # answer. Several offline clients distinguish a missing field
                # from a deliberately hidden answer.
                question["correct"] = None
        return manifest

    payload = manifest_cache.get_or_build(cache_key, build_manifest)
    if storage is not None:
        asset_ids = manifest_cache.get_or_build(
            str(version.id),
            lambda: {
                reference: asset_id
                for asset_id, filename, object_key in session.execute(
                    select(
                        ExamVersionAsset.id,
                        ExamVersionAsset.filename,
                        ExamVersionAsset.object_key,
                    ).where(ExamVersionAsset.exam_version_id == version.id)
                )
                for reference in {
                    filename,
                    str(object_key).rsplit("/", 1)[-1],
                }
                if reference
            },
            namespace="examify-assets:v1",
        )
        for stimulus in payload.get("stimuli") or []:
            for asset in stimulus.get("assets") or []:
                reference = str(asset.get("id") or "").strip()
                asset_id = asset_ids.get(reference)
                if asset_id:
                    asset["url"] = _class_asset_url(
                        asset_id, version.id, assignment, member_id
                    )
        for audio in payload.get("audios") or []:
            reference = str(audio.get("id") or "").strip()
            asset_id = asset_ids.get(reference)
            if asset_id:
                audio["url"] = _class_asset_url(
                    asset_id, version.id, assignment, member_id
                )
        if payload.get("audio"):
            reference = str(payload["audio"].get("id") or "").strip()
            asset_id = asset_ids.get(reference)
            if asset_id:
                payload["audio"]["url"] = _class_asset_url(
                    asset_id, version.id, assignment, member_id
                )
    return payload


@router.get("/class-assets/{asset_id}")
def class_asset(
    asset_id: str,
    token: str,
    request: Request,
):
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Liên kết tài nguyên không hợp lệ") from exc
    if claims.get("type") != "class_asset" or claims.get("asset_id") != asset_id:
        raise HTTPException(status_code=401, detail="Sai loại liên kết tài nguyên")
    assignment_id = str(claims.get("assignment_id") or "")
    member_id = str(claims.get("member_id") or "")
    token_hash = sha256(token)
    cached = (
        media_auth_cache.get(token_hash)
        if assignment_id != "preview" and member_id != "preview"
        else None
    )
    if cached:
        bucket = str(cached["bucket"])
        object_key = str(cached["object_key"])
        content_type = str(cached["content_type"])
        total_size = int(cached["size"] or 0)
    else:
        with session_scope() as session:
            assignment_id = claims.get("assignment_id")
            member_id = claims.get("member_id")
            if assignment_id != "preview" and member_id != "preview":
                asset = session.scalar(
                    select(ExamVersionAsset)
                    .join(
                        ClassAssignment,
                        ClassAssignment.id == assignment_id,
                    )
                    .join(ClassMember, ClassMember.id == member_id)
                    .where(
                        ExamVersionAsset.id == asset_id,
                        ExamVersionAsset.exam_version_id == claims.get("version_id"),
                        ClassAssignment.exam_version_id == ExamVersionAsset.exam_version_id,
                        ClassAssignment.classroom_id == ClassMember.classroom_id,
                        ClassMember.status == "active",
                    )
                )
            else:
                asset = session.scalar(
                    select(ExamVersionAsset).where(
                        ExamVersionAsset.id == asset_id,
                        ExamVersionAsset.exam_version_id == claims.get("version_id"),
                    )
                )
            if asset is None:
                raise HTTPException(status_code=404, detail="Không tìm thấy tài nguyên")
            bucket = asset.bucket
            object_key = asset.object_key
            content_type = asset.content_type
            total_size = asset.size
            if not content_type or content_type == "application/octet-stream":
                # Older imports stored generic content types.  Version assets
                # use opaque UUIDs in their public URL, so infer from the
                # original filename/object key before sending the response;
                # otherwise browsers refuse to render WebP or play media.
                content_type = (
                    mimetypes.guess_type(asset.filename or "")[0]
                    or mimetypes.guess_type(object_key or "")[0]
                    or content_type
                )
        if assignment_id != "preview" and member_id != "preview":
            media_auth_cache.put(
                token_hash,
                {
                    "bucket": bucket,
                    "object_key": object_key,
                    "content_type": content_type,
                    "size": total_size,
                },
                member_id=str(member_id),
                assignment_id=str(assignment_id),
            )
    if not content_type or content_type == "application/octet-stream":
        # A previously cached authorization entry can still contain the
        # generic type from a legacy row; the object key is authoritative.
        content_type = mimetypes.guess_type(object_key or "")[0] or content_type
    if storage is None:
        raise HTTPException(status_code=503, detail="Object storage chưa sẵn sàng")
    safe_object_key = storage.safe_key(object_key)
    if settings.minio_accel_redirect_prefix:
        internal_path = storage.presigned_internal_redirect(
            bucket,
            safe_object_key,
            settings.minio_accel_redirect_prefix,
            method=request.method,
        )
        return Response(
            headers={
                "X-Accel-Redirect": internal_path,
                "X-Accel-Expires": "3600",
                "Cache-Control": "private, max-age=3600",
                "Accept-Ranges": "bytes",
                "Content-Type": content_type,
            }
        )
    if not total_size:
        total_size = storage.client.stat_object(
            bucket, safe_object_key
        ).size

    start = 0
    end = max(0, total_size - 1)
    status_code = 200
    range_header = request.headers.get("range", "")
    if range_header.startswith("bytes=") and "," not in range_header:
        requested = range_header[6:].split("-", 1)
        try:
            if requested[0]:
                start = int(requested[0])
                end = int(requested[1]) if requested[1] else end
            elif requested[1]:
                suffix = min(int(requested[1]), total_size)
                start = total_size - suffix
            if start < 0 or start >= total_size or end < start:
                raise ValueError
            end = min(end, total_size - 1)
            status_code = 206
        except ValueError as exc:
            raise HTTPException(status_code=416, detail="Khoảng dữ liệu không hợp lệ") from exc
    length = end - start + 1
    minio_response = storage.client.get_object(
        bucket,
        safe_object_key,
        offset=start,
        length=length,
    )

    def body():
        try:
            yield from minio_response.stream(1024 * 1024)
        finally:
            minio_response.close()
            minio_response.release_conn()

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "private, max-age=3600",
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{total_size}"
    return StreamingResponse(
        body(),
        status_code=status_code,
        media_type=content_type,
        headers=headers,
    )


def _release_visible(assignment: ClassAssignment, kind: str) -> bool:
    if _is_study_resource(assignment):
        return True
    policy = assignment.score_release if kind == "score" else assignment.answer_release
    released_at = (
        assignment.results_released_at
        if kind == "score"
        else assignment.answers_released_at
    )
    if policy == "immediate":
        return True
    if policy == "never":
        return False
    if released_at is not None:
        return True
    if policy == "manual":
        return False
    now = utcnow()
    closes_at = _aware(assignment.closes_at)
    return assignment.status == "closed" or bool(closes_at and closes_at <= now)


def _owned_class_attempt(
    session: Any,
    attempt_id: str,
    member: ClassMember,
    *,
    lock: bool = False,
) -> tuple[Attempt, ClassAssignment, ExamVersion]:
    query = (
        select(Attempt, ClassAssignment, ExamVersion)
        .join(ClassAssignment, ClassAssignment.id == Attempt.class_assignment_id)
        .join(ExamVersion, ExamVersion.id == ClassAssignment.exam_version_id)
        .where(
            Attempt.id == attempt_id,
            Attempt.class_member_id == member.id,
            ClassAssignment.classroom_id == member.classroom_id,
        )
    )
    if lock:
        query = query.with_for_update(of=Attempt)
    row = session.execute(query).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài được giao")
    attempt, assignment, version = row
    return attempt, assignment, version


def _store_answers(
    session: Any,
    attempt: Attempt,
    version: ExamVersion,
    body: AttemptAnswersRequest,
    *,
    force: bool = False,
    question_rows: list[tuple[int, int | None, str | None]] | None = None,
    grade: bool = False,
) -> dict[int, str] | None:
    current_revision = int(attempt.answer_revision or 0)
    if (
        not force
        and body.client_revision is not None
        and body.client_revision <= current_revision
    ):
        return None
    rows = question_rows or _version_question_rows(session, version, attempt)
    allowed_numbers = {number for number, _part, _correct in rows}
    try:
        answers = normalize_attempt_answers(body.answers, allowed_numbers)
    except InvalidAttemptAnswer as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    correct_by_number = (
        {number: correct for number, _part, correct in rows} if grade else None
    )
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


def _version_question_rows(
    session: Any,
    version: ExamVersion,
    attempt: Attempt | None = None,
) -> list[tuple[int, int | None, str | None]]:
    query = select(
        ExamVersionQuestion.question_number,
        ExamVersionQuestion.part_number,
        ExamVersionQuestion.correct,
    ).where(ExamVersionQuestion.exam_version_id == version.id)
    if attempt and attempt.selected_part_numbers:
        query = query.where(
            ExamVersionQuestion.part_number.in_(
                [int(part) for part in attempt.selected_part_numbers]
            )
        )
    rows = [(int(number), part, correct) for number, part, correct in session.execute(query)]
    if rows:
        return rows

    # Expand/contract compatibility for an old app process that created a
    # version immediately before migration. New snapshots always populate the
    # projection, so this JSON fallback is not used on the steady-state path.
    return [
        (
            int(question.get("number", 0)),
            _part_number_for_question(int(question.get("number", 0))),
            (
                str(question.get("correct")).strip().upper()
                if question.get("correct")
                else None
            ),
        )
        for question in _attempt_payload(version, attempt).get("questions") or []
        if int(question.get("number", 0)) > 0
    ]


def _allowed_version_question_numbers(
    session: Any,
    version: ExamVersion,
    attempt: Attempt | None = None,
    raw_changes: dict[str, str | None] | None = None,
) -> set[int]:
    if raw_changes is not None:
        candidates: set[int] = set()
        for raw_number in raw_changes:
            try:
                number = int(raw_number)
            except (TypeError, ValueError):
                continue
            if str(number) == str(raw_number).strip() and 1 <= number <= 200:
                candidates.add(number)
        if not candidates:
            return set()
        query = select(ExamVersionQuestion.question_number).where(
            ExamVersionQuestion.exam_version_id == version.id,
            ExamVersionQuestion.question_number.in_(candidates),
        )
        if attempt and attempt.selected_part_numbers:
            query = query.where(
                ExamVersionQuestion.part_number.in_(
                    [int(part) for part in attempt.selected_part_numbers]
                )
            )
        rows = {int(number) for number in session.scalars(query)}
        if rows:
            return rows
        projection_exists = session.scalar(
            select(ExamVersionQuestion.id)
            .where(ExamVersionQuestion.exam_version_id == version.id)
            .limit(1)
        )
        if projection_exists:
            return set()
        # Compatibility only for versions created before the projection
        # migration; keep the delta bounded after reading legacy JSON.
        return {
            number
            for number, _part_number, _correct in _version_question_rows(
                session, version, attempt
            )
            if number in candidates
        }
    return {
        number for number, _part_number, _correct in _version_question_rows(
            session, version, attempt
        )
    }


def _finalize_attempt(
    session: Any,
    attempt: Attempt,
    assignment: ClassAssignment,
    version: ExamVersion,
    *,
    reason: str,
    question_rows: list[tuple[int, int | None, str | None]] | None = None,
    submitted_answers: dict[int, str] | None = None,
) -> dict[int, str]:
    if attempt.status == "submitted":
        return submitted_answers or {}
    correct_by_number = {
        number: correct
        for number, _part_number, correct in (
            question_rows or _version_question_rows(session, version, attempt)
        )
    }
    answer_rows: list[AttemptAnswer] = []
    if submitted_answers is None:
        answer_rows = list(
            session.scalars(
                select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id)
            )
        )
        submitted_answers = {
            answer.question_number: answer.selected for answer in answer_rows
        }
    listening_total = sum(
        bool(answer) and number <= 100
        for number, answer in correct_by_number.items()
    )
    reading_total = sum(
        bool(answer) and number >= 101
        for number, answer in correct_by_number.items()
    )
    listening_correct = 0
    reading_correct = 0
    correct = 0
    graded = listening_total + reading_total
    for number, selected in submitted_answers.items():
        expected = correct_by_number.get(number)
        if expected:
            is_correct = selected == expected
            correct += int(is_correct)
            if number <= 100:
                listening_correct += int(is_correct)
            else:
                reading_correct += int(is_correct)
    if answer_rows:
        for answer in answer_rows:
            expected = correct_by_number.get(answer.question_number)
            answer.is_correct = answer.selected == expected if expected else None
    listening, reading, total_score = toeic_scores(
        listening_correct,
        listening_total,
        reading_correct,
        reading_total,
    )
    now = utcnow()
    attempt.status = "submitted"
    attempt.correct_count = correct
    attempt.graded_count = graded
    attempt.listening_score = listening
    attempt.reading_score = reading
    attempt.score_toeic = total_score
    attempt.time_spent_seconds = max(
        0, attempt.duration_seconds - (attempt.time_left_seconds or 0)
    )
    attempt.submit_reason = reason
    attempt.submitted_at = now
    attempt.submit_receipt_id = attempt.submit_receipt_id or uuid4()
    attempt.submitted_answer_hash = sha256(
        json.dumps(
            {
                str(number): submitted_answers[number]
                for number in sorted(submitted_answers)
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    attempt.updated_at = now
    return submitted_answers


def _attempt_result(
    session: Any,
    attempt: Attempt,
    assignment: ClassAssignment,
    version: ExamVersion,
) -> dict[str, Any]:
    score_visible = _release_visible(assignment, "score")
    answers_visible = _release_visible(assignment, "answer")
    selected = {
        item.question_number: item.selected
        for item in session.scalars(
            select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id)
        )
    }
    result = {
        "attempt_id": attempt.id,
        "schema_version": 2,
        "exam": _exam_for_student(
            session,
            version,
            answers_visible,
            assignment=assignment,
            member_id=str(attempt.class_member_id),
            attempt=attempt,
        ),
        "answers": selected,
        "duration_seconds": attempt.duration_seconds,
        "time_left_seconds": attempt.time_left_seconds,
        "submitted_at": attempt.submitted_at,
        "status": attempt.status,
        "accepted_revision": attempt.answer_revision,
        "receipt_id": attempt.submit_receipt_id,
        "launch_mode": attempt.launch_mode
        or ("practice" if _is_study_resource(assignment) else "official_exam"),
        "selected_part_numbers": attempt.selected_part_numbers
        or _available_part_numbers(version.payload or {}),
        "score_released": score_visible,
        "answers_released": answers_visible,
        "has_solutions": bool((version.payload or {}).get("solutions")),
    }
    if score_visible and attempt.status == "submitted":
        result["scores"] = {
            "toeic": attempt.score_toeic,
            "listening": attempt.listening_score,
            "reading": attempt.reading_score,
            "correct": attempt.correct_count,
            "graded": attempt.graded_count,
        }
    return result


def _attempt_receipt(
    session: Any,
    attempt: Attempt,
    assignment: ClassAssignment,
    version: ExamVersion,
    submitted_answers: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Return the durable submit receipt without re-sending the whole exam.

    The browser already owns the immutable exam snapshot. Returning it again
    for every submit (including idempotent retries) made a synchronized submit
    burst spend most of its time copying and serializing identical content.
    """
    score_visible = _release_visible(assignment, "score")
    answers_visible = _release_visible(assignment, "answer")
    result: dict[str, Any] = {
        "attempt_id": attempt.id,
        "schema_version": 2,
        "answers": submitted_answers
        if submitted_answers is not None
        else {
            item.question_number: item.selected
            for item in session.scalars(
                select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id)
            )
        },
        "duration_seconds": attempt.duration_seconds,
        "time_left_seconds": attempt.time_left_seconds,
        "submitted_at": attempt.submitted_at,
        "status": attempt.status,
        "accepted_revision": attempt.answer_revision,
        "receipt_id": attempt.submit_receipt_id,
        "launch_mode": attempt.launch_mode
        or ("practice" if _is_study_resource(assignment) else "official_exam"),
        "selected_part_numbers": attempt.selected_part_numbers
        or _available_part_numbers(version.payload or {}),
        "score_released": score_visible,
        "answers_released": answers_visible,
        "has_solutions": bool((version.payload or {}).get("solutions")),
    }
    if score_visible and attempt.status == "submitted":
        result["scores"] = {
            "toeic": attempt.score_toeic,
            "listening": attempt.listening_score,
            "reading": attempt.reading_score,
            "correct": attempt.correct_count,
            "graded": attempt.graded_count,
        }
    return result


@router.get("/teacher/classrooms")
def list_teacher_classrooms(request: Request) -> dict[str, Any]:
    identity = require_teacher(request)
    uid = identity["user_id"]
    with session_scope() as session:
        owned = session.scalars(
            select(Classroom)
            .where(Classroom.owner_teacher_id == uid)
            .order_by(Classroom.updated_at.desc())
        ).all()
        co_taught_ids = list(
            session.scalars(
                select(ClassroomCoTeacher.classroom_id).where(
                    ClassroomCoTeacher.teacher_user_id == uid,
                    ClassroomCoTeacher.status == "active",
                )
            )
        )
        co_taught = (
            session.scalars(
                select(Classroom)
                .where(Classroom.id.in_(co_taught_ids))
                .order_by(Classroom.updated_at.desc())
            ).all()
            if co_taught_ids
            else []
        )
        owned_ids = {row.id for row in owned}
        all_rows = owned + [r for r in co_taught if r.id not in owned_ids]
        member_counts, assignment_counts = _classroom_counts(
            session, [row.id for row in all_rows]
        )
        return {
            "items": [
                _classroom_payload(
                    session,
                    row,
                    member_count=member_counts.get(row.id, 0),
                    assignment_count=assignment_counts.get(row.id, 0),
                    teacher_id=uid,
                )
                for row in all_rows
            ]
        }


@router.post("/teacher/classrooms")
def create_teacher_classroom(
    body: ClassroomCreateRequest, request: Request
) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        classroom = Classroom(
            owner_teacher_id=identity["user_id"],
            name=body.name.strip(),
            description=body.description.strip(),
            join_code=_unique_join_code(session),
        )
        session.add(classroom)
        session.flush()
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="classroom.created",
                target_type="classroom",
                target_id=classroom.id,
            )
        )
        return _classroom_payload(session, classroom)


@router.get("/teacher/classrooms/{classroom_id}")
def get_teacher_classroom(classroom_id: str, request: Request) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        classroom = _accessible_classroom(session, classroom_id, identity["user_id"])
        result = _classroom_payload(session, classroom, teacher_id=identity["user_id"])
        assignments = session.execute(
            select(ClassAssignment, ExamVersion)
            .join(ExamVersion, ExamVersion.id == ClassAssignment.exam_version_id)
            .where(ClassAssignment.classroom_id == classroom.id)
            .order_by(ClassAssignment.created_at.desc())
        ).all()
        result["assignments"] = [
            _assignment_payload(assignment, version)
            for assignment, version in assignments
        ]
        return result


@router.patch("/teacher/classrooms/{classroom_id}")
def update_teacher_classroom(
    classroom_id: str, body: ClassroomUpdateRequest, request: Request
) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        # Status changes (archive/unarchive) are owner-only
        if body.status is not None:
            classroom = _owned_classroom(session, classroom_id, identity["user_id"])
        else:
            classroom = _accessible_classroom(session, classroom_id, identity["user_id"])
        if body.name is not None:
            classroom.name = body.name.strip()
        if body.description is not None:
            classroom.description = body.description.strip()
        if body.status is not None:
            classroom.status = body.status
        classroom.updated_at = utcnow()
        return _classroom_payload(session, classroom, teacher_id=identity["user_id"])


@router.post("/teacher/classrooms/{classroom_id}/regenerate-code")
def regenerate_classroom_code(classroom_id: str, request: Request) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        classroom = _accessible_classroom(session, classroom_id, identity["user_id"])
        classroom.join_code = _unique_join_code(session)
        classroom.updated_at = utcnow()
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="classroom.code_regenerated",
                target_type="classroom",
                target_id=classroom.id,
            )
        )
        return {"join_code": classroom.join_code}


class CoTeacherAddRequest(BaseModel):
    teacher_user_id: str = Field(min_length=1, max_length=36)


@router.get("/teacher/classrooms/{classroom_id}/search-teacher")
def search_teacher_for_classroom(
    classroom_id: str, email: str, request: Request
) -> dict[str, Any]:
    """Search a teacher by email for co-teacher invitation."""
    identity = require_teacher(request)
    with session_scope() as session:
        _owned_classroom(session, classroom_id, identity["user_id"])
        email_clean = email.strip().lower()
        if not email_clean:
            return {"found": False}
        user = session.scalar(
            select(User).where(
                func.lower(User.email) == email_clean,
                User.role == "teacher",
                User.status == "active",
            )
        )
        if user is None or user.id == identity["user_id"]:
            return {"found": False}
        existing = session.scalar(
            select(ClassroomCoTeacher).where(
                ClassroomCoTeacher.classroom_id == classroom_id,
                ClassroomCoTeacher.teacher_user_id == user.id,
                ClassroomCoTeacher.status == "active",
            )
        )
        return {
            "found": True,
            "user_id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "already_co_teacher": existing is not None,
        }


@router.post("/teacher/classrooms/{classroom_id}/co-teachers")
def add_co_teacher(
    classroom_id: str, body: CoTeacherAddRequest, request: Request
) -> dict[str, Any]:
    """Invite a teacher as co-teacher.  Owner only."""
    identity = require_teacher(request)
    with session_scope() as session:
        _owned_classroom(session, classroom_id, identity["user_id"])
        teacher = session.get(User, body.teacher_user_id)
        if (
            teacher is None
            or teacher.role != "teacher"
            or teacher.status != "active"
        ):
            raise HTTPException(
                status_code=404, detail="Không tìm thấy giáo viên"
            )
        if teacher.id == identity["user_id"]:
            raise HTTPException(
                status_code=422, detail="Không thể mời chính mình"
            )
        existing = session.scalar(
            select(ClassroomCoTeacher).where(
                ClassroomCoTeacher.classroom_id == classroom_id,
                ClassroomCoTeacher.teacher_user_id == teacher.id,
            )
        )
        if existing:
            if existing.status == "active":
                raise HTTPException(
                    status_code=409,
                    detail="Giáo viên đã là co-teacher của lớp này",
                )
            # Reactivate previously removed co-teacher
            existing.status = "active"
            existing.invited_by_user_id = identity["user_id"]
            existing.created_at = utcnow()
        else:
            session.add(
                ClassroomCoTeacher(
                    classroom_id=classroom_id,
                    teacher_user_id=teacher.id,
                    invited_by_user_id=identity["user_id"],
                )
            )
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="classroom.co_teacher_added",
                target_type="classroom_co_teacher",
                target_id=classroom_id,
                detail={"teacher_user_id": teacher.id},
            )
        )
        session.flush()
        return {
            "teacher_user_id": teacher.id,
            "email": teacher.email,
            "display_name": teacher.display_name,
            "status": "active",
        }


@router.get("/teacher/classrooms/{classroom_id}/co-teachers")
def list_co_teachers(
    classroom_id: str, request: Request
) -> dict[str, Any]:
    """List all co-teachers.  Visible to owner and co-teachers."""
    identity = require_teacher(request)
    with session_scope() as session:
        classroom = _accessible_classroom(
            session, classroom_id, identity["user_id"]
        )
        rows = session.scalars(
            select(ClassroomCoTeacher).where(
                ClassroomCoTeacher.classroom_id == classroom_id,
                ClassroomCoTeacher.status == "active",
            )
        ).all()
        teacher_ids = [row.teacher_user_id for row in rows]
        teachers = (
            {
                user.id: user
                for user in session.scalars(
                    select(User).where(User.id.in_(teacher_ids))
                )
            }
            if teacher_ids
            else {}
        )
        owner = session.get(User, classroom.owner_teacher_id)
        return {
            "owner": {
                "user_id": owner.id,
                "email": owner.email,
                "display_name": owner.display_name,
            }
            if owner
            else None,
            "items": [
                {
                    "id": row.id,
                    "teacher_user_id": row.teacher_user_id,
                    "email": teachers[row.teacher_user_id].email
                    if row.teacher_user_id in teachers
                    else None,
                    "display_name": teachers[row.teacher_user_id].display_name
                    if row.teacher_user_id in teachers
                    else None,
                    "status": row.status,
                    "created_at": row.created_at,
                }
                for row in rows
            ],
        }


@router.delete("/teacher/classrooms/{classroom_id}/co-teachers/{co_teacher_id}")
def remove_co_teacher(
    classroom_id: str, co_teacher_id: str, request: Request
) -> dict[str, Any]:
    """Remove a co-teacher.  Owner only."""
    identity = require_teacher(request)
    with session_scope() as session:
        _owned_classroom(session, classroom_id, identity["user_id"])
        co = session.get(ClassroomCoTeacher, co_teacher_id)
        if (
            co is None
            or co.classroom_id != classroom_id
            or co.status != "active"
        ):
            raise HTTPException(
                status_code=404, detail="Không tìm thấy co-teacher"
            )
        co.status = "removed"
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="classroom.co_teacher_removed",
                target_type="classroom_co_teacher",
                target_id=co.id,
                detail={"teacher_user_id": co.teacher_user_id},
            )
        )
        return {"ok": True}


@router.post("/teacher/classrooms/{classroom_id}/co-teachers/leave")
def leave_co_teacher(
    classroom_id: str, request: Request
) -> dict[str, Any]:
    """Co-teacher leaves a classroom voluntarily."""
    identity = require_teacher(request)
    with session_scope() as session:
        classroom = session.get(Classroom, classroom_id)
        if classroom is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy lớp học")
        if classroom.owner_teacher_id == identity["user_id"]:
            raise HTTPException(
                status_code=422,
                detail="Owner không thể rời lớp. Hãy chuyển quyền hoặc xóa lớp.",
            )
        co = session.scalar(
            select(ClassroomCoTeacher).where(
                ClassroomCoTeacher.classroom_id == classroom_id,
                ClassroomCoTeacher.teacher_user_id == identity["user_id"],
                ClassroomCoTeacher.status == "active",
            )
        )
        if co is None:
            raise HTTPException(
                status_code=404, detail="Bạn không phải co-teacher của lớp này"
            )
        co.status = "removed"
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="classroom.co_teacher_left",
                target_type="classroom_co_teacher",
                target_id=co.id,
                detail={"classroom_id": classroom_id},
            )
        )
        return {"ok": True}


@router.get("/teacher/classrooms/{classroom_id}/members")
def list_class_members(
    classroom_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        _accessible_classroom(session, classroom_id, identity["user_id"])
        filters = (ClassMember.classroom_id == classroom_id,)
        total = int(session.scalar(select(func.count(ClassMember.id)).where(*filters)) or 0)
        rows = session.scalars(
            select(ClassMember)
            .where(*filters)
            .order_by(ClassMember.joined_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {
            "items": [
                {
                    "id": row.id,
                    "member_ref": row.id[:6].upper(),
                    "full_name": row.full_name,
                    "status": row.status,
                    "joined_at": row.joined_at,
                    "last_seen_at": row.last_seen_at,
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
        }


@router.patch("/teacher/classrooms/{classroom_id}/members/{member_id}")
def update_class_member(
    classroom_id: str,
    member_id: str,
    body: MemberStatusRequest,
    request: Request,
) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        _accessible_classroom(session, classroom_id, identity["user_id"])
        member = session.get(ClassMember, member_id)
        if member is None or member.classroom_id != classroom_id:
            raise HTTPException(status_code=404, detail="Không tìm thấy học viên")
        member.status = body.status
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action=f"classroom.member_{body.status}",
                target_type="class_member",
                target_id=member.id,
            )
        )
        payload = {"id": member.id, "status": member.status}
    media_auth_cache.invalidate_member(member_id)
    return payload


@router.post("/teacher/classrooms/{classroom_id}/assignments")
def create_class_assignment(
    classroom_id: str, body: AssignmentCreateRequest, request: Request
) -> dict[str, Any]:
    identity = require_teacher(request)
    opens_at = _aware(body.opens_at)
    closes_at = _aware(body.closes_at)
    if opens_at and closes_at and closes_at <= opens_at:
        raise HTTPException(status_code=422, detail="Thời gian đóng phải sau thời gian mở")
    with session_scope() as session:
        classroom = _accessible_classroom(session, classroom_id, identity["user_id"])
        if classroom.status != "active":
            raise HTTPException(status_code=409, detail="Lớp đang lưu trữ")
        exam = session.scalar(
            select(Exam).where(Exam.id == body.exam_id).with_for_update()
        )
        if (
            exam is None
            or exam.library_scope != "teacher_shared"
            or exam.deleted_at is not None
            or exam.owner_user_id != identity["user_id"]
        ):
            raise HTTPException(status_code=404, detail="Không tìm thấy đề trong kho")
        if body.mode == "exam" and exam.answer_key_count < exam.question_count:
            raise HTTPException(
                status_code=422,
                detail="Chế độ thi yêu cầu đáp án đầy đủ cho tất cả câu hỏi",
            )
        version = (
            session.get(ExamVersion, exam.current_version_id)
            if exam.current_version_id
            else None
        )
        if version is None:
            version = _snapshot_exam(session, exam, identity["user_id"])
            exam.current_version_id = version.id
        duration_seconds = body.duration_seconds or (
            120 * 60 if body.mode == "exam" else exam.duration_minutes * 60
        )
        attempt_limit = None if body.mode == "practice" else (body.attempt_limit or 1)
        assignment = ClassAssignment(
            classroom_id=classroom.id,
            exam_version_id=version.id,
            title=(body.title or exam.title).strip(),
            mode=body.mode,
            status="published" if body.publish else "draft",
            opens_at=opens_at,
            closes_at=closes_at,
            duration_seconds=duration_seconds,
            attempt_limit=attempt_limit,
            score_release=body.score_release,
            answer_release=body.answer_release,
            anti_cheat_enabled=body.anti_cheat_enabled,
            listening_navigation_locked=body.listening_navigation_locked,
            published_at=utcnow() if body.publish else None,
        )
        session.add(assignment)
        session.flush()
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="classroom.assignment_created",
                target_type="class_assignment",
                target_id=assignment.id,
                detail={"classroom_id": classroom.id, "exam_version_id": version.id},
            )
        )
        return _assignment_payload(assignment, version)


@router.patch("/teacher/classrooms/{classroom_id}/assignments/{assignment_id}")
def update_class_assignment(
    classroom_id: str,
    assignment_id: str,
    body: AssignmentUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        _accessible_classroom(session, classroom_id, identity["user_id"])
        assignment = session.get(ClassAssignment, assignment_id)
        if assignment is None or assignment.classroom_id != classroom_id:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài được giao")
        attempts = session.scalar(
            select(func.count(Attempt.id)).where(
                Attempt.class_assignment_id == assignment.id
            )
        ) or 0
        values = body.model_dump(exclude_unset=True)
        mutable_after_start = {
            "title",
            "attempt_limit",
            "score_release",
            "answer_release",
        }
        immutable_after_start = set(values) - mutable_after_start
        if attempts and immutable_after_start:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Sau khi đã có lượt làm, chỉ có thể đổi tên, số lượt và "
                    "chính sách công bố"
                ),
            )
        final_mode = values.get("mode", assignment.mode)
        if final_mode == "practice":
            values["attempt_limit"] = None
        elif "attempt_limit" in values and values["attempt_limit"] is None:
            values["attempt_limit"] = 1
        if attempts and values.get("attempt_limit") is not None:
            max_attempt_number = session.scalar(
                select(func.max(Attempt.attempt_number)).where(
                    Attempt.class_assignment_id == assignment.id
                )
            ) or 0
            if values["attempt_limit"] < max_attempt_number:
                raise HTTPException(
                    status_code=422,
                    detail=f"Số lượt không thể nhỏ hơn {max_attempt_number} lượt đã phát sinh",
                )
        for key, value in values.items():
            if key in {"opens_at", "closes_at"}:
                value = _aware(value)
            setattr(assignment, key, value)
        if (
            assignment.opens_at
            and assignment.closes_at
            and _aware(assignment.closes_at) <= _aware(assignment.opens_at)
        ):
            raise HTTPException(status_code=422, detail="Thời gian đóng phải sau thời gian mở")
        assignment.updated_at = utcnow()
        return _assignment_payload(
            assignment, session.get(ExamVersion, assignment.exam_version_id)
        )


@router.post("/teacher/classrooms/{classroom_id}/assignments/{assignment_id}/publish")
def publish_class_assignment(
    classroom_id: str, assignment_id: str, request: Request
) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        _accessible_classroom(session, classroom_id, identity["user_id"])
        assignment = session.get(ClassAssignment, assignment_id)
        if assignment is None or assignment.classroom_id != classroom_id:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài được giao")
        assignment.status = "published"
        assignment.published_at = assignment.published_at or utcnow()
        return {"id": assignment.id, "status": assignment.status}


@router.post("/teacher/classrooms/{classroom_id}/assignments/{assignment_id}/close")
def close_class_assignment(
    classroom_id: str, assignment_id: str, request: Request
) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        _accessible_classroom(session, classroom_id, identity["user_id"])
        assignment = session.get(ClassAssignment, assignment_id)
        if assignment is None or assignment.classroom_id != classroom_id:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài được giao")
        assignment.status = "closed"
        assignment.updated_at = utcnow()
        payload = {"id": assignment.id, "status": assignment.status}
    media_auth_cache.invalidate_assignment(assignment_id)
    return payload


@router.post("/teacher/classrooms/{classroom_id}/assignments/{assignment_id}/reopen")
def reopen_class_assignment(
    classroom_id: str,
    assignment_id: str,
    body: AssignmentReopenRequest,
    request: Request,
) -> dict[str, Any]:
    identity = require_teacher(request)
    now = utcnow()
    closes_at = _aware(body.closes_at)
    if closes_at and closes_at <= now:
        raise HTTPException(status_code=422, detail="Thời gian đóng mới phải ở tương lai")
    with session_scope() as session:
        _accessible_classroom(session, classroom_id, identity["user_id"])
        assignment = session.get(ClassAssignment, assignment_id)
        if assignment is None or assignment.classroom_id != classroom_id:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài được giao")
        max_attempt_number = session.scalar(
            select(func.max(Attempt.attempt_number)).where(
                Attempt.class_assignment_id == assignment.id
            )
        ) or 0
        if assignment.mode == "practice":
            assignment.attempt_limit = None
        else:
            assignment.attempt_limit = max(
                assignment.attempt_limit or 1,
                max_attempt_number + body.additional_attempts,
            )
        assignment.status = "published"
        assignment.opens_at = now
        assignment.closes_at = closes_at
        assignment.published_at = assignment.published_at or now
        assignment.updated_at = now
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="classroom.assignment_reopened",
                target_type="class_assignment",
                target_id=assignment.id,
                detail={
                    "additional_attempts": body.additional_attempts,
                    "closes_at": closes_at.isoformat() if closes_at else None,
                },
            )
        )
        version = session.get(ExamVersion, assignment.exam_version_id)
        return _assignment_payload(assignment, version)


@router.post("/teacher/classrooms/{classroom_id}/assignments/{assignment_id}/release")
def release_assignment_results(
    classroom_id: str,
    assignment_id: str,
    request: Request,
    answers: bool = False,
) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        _accessible_classroom(session, classroom_id, identity["user_id"])
        assignment = session.get(ClassAssignment, assignment_id)
        if assignment is None or assignment.classroom_id != classroom_id:
            raise HTTPException(status_code=404, detail="Không tìm thấy bài được giao")
        assignment.results_released_at = utcnow()
        if answers:
            assignment.answers_released_at = utcnow()
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="classroom.results_released",
                target_type="class_assignment",
                target_id=assignment.id,
                detail={"answers": answers},
            )
        )
        return {"ok": True, "answers_released": answers}


@router.get("/teacher/classrooms/{classroom_id}/monitoring")
def classroom_monitoring(
    classroom_id: str,
    request: Request,
    assignment_id: str | None = None,
    history_limit: int = Query(default=500, ge=0, le=2000),
) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        _accessible_classroom(session, classroom_id, identity["user_id"])
        members = session.scalars(
            select(ClassMember)
            .where(ClassMember.classroom_id == classroom_id)
            .order_by(ClassMember.full_name)
        ).all()
        assignments = session.scalars(
            select(ClassAssignment).where(
                ClassAssignment.classroom_id == classroom_id,
                ClassAssignment.publication_kind.is_(None),
                ClassAssignment.mode == "exam",
            )
        ).all()
        assignment_by_id = {row.id: row for row in assignments}
        assignment_ids = list(assignment_by_id)
        if assignment_id:
            if assignment_id not in assignment_ids:
                raise HTTPException(status_code=404, detail="Không tìm thấy bài được giao")
            assignment_ids = [assignment_id]
        history_attempts = (
            session.scalars(
                select(Attempt)
                .where(Attempt.class_assignment_id.in_(assignment_ids))
                .order_by(Attempt.started_at.desc())
                .limit(history_limit)
            ).all()
            if assignment_ids and history_limit
            else []
        )
        latest_attempts: list[Attempt] = []
        if assignment_ids:
            ranked = (
                select(
                    Attempt.id.label("attempt_id"),
                    func.row_number()
                    .over(
                        partition_by=Attempt.class_member_id,
                        order_by=Attempt.started_at.desc(),
                    )
                    .label("row_number"),
                )
                .where(Attempt.class_assignment_id.in_(assignment_ids))
                .subquery()
            )
            latest_attempts = session.scalars(
                select(Attempt)
                .join(ranked, ranked.c.attempt_id == Attempt.id)
                .where(ranked.c.row_number == 1)
            ).all()
        by_member = {
            str(attempt.class_member_id): attempt for attempt in latest_attempts
        }
        live_presence = presence_store.get_many(
            [attempt.id for attempt in latest_attempts]
        )
        visible_attempts = {
            attempt.id: attempt
            for attempt in [*history_attempts, *latest_attempts]
        }
        event_counts = dict(
            session.execute(
                select(AntiCheatEvent.attempt_id, func.count(AntiCheatEvent.id))
                .where(AntiCheatEvent.attempt_id.in_(visible_attempts))
                .group_by(AntiCheatEvent.attempt_id)
            ).all()
        ) if visible_attempts else {}
        total_attempts = 0
        completed_attempts = 0
        average_score = 0
        highest_score = 0
        if assignment_ids:
            total_attempts, completed_attempts = session.execute(
                select(
                    func.count(Attempt.id),
                    func.count(Attempt.id).filter(Attempt.status == "submitted"),
                ).where(Attempt.class_assignment_id.in_(assignment_ids))
            ).one()
            average_value, highest_value = session.execute(
                select(func.avg(Attempt.score_toeic), func.max(Attempt.score_toeic)).where(
                    Attempt.class_assignment_id.in_(assignment_ids),
                    Attempt.score_toeic.is_not(None),
                )
            ).one()
            average_score = round(float(average_value or 0))
            highest_score = int(highest_value or 0)
        now = utcnow()
        def attempt_state(attempt: Attempt) -> str:
            if attempt.status == "submitted":
                return "submitted"
            if attempt.id in live_presence:
                return "in_progress"
            heartbeat = _aware(attempt.last_heartbeat_at)
            return (
                "in_progress"
                if heartbeat
                and now - heartbeat <= timedelta(seconds=PRESENCE_TTL_SECONDS)
                else "disconnected"
            )

        items = []
        for member in members:
            latest = by_member.get(member.id)
            presence = live_presence.get(latest.id, {}) if latest else {}
            state = "not_started"
            if latest:
                state = attempt_state(latest)
            items.append(
                {
                    "member_id": member.id,
                    "member_ref": member.id[:6].upper(),
                    "full_name": member.full_name,
                    "member_status": member.status,
                    "state": state,
                    "attempt_id": latest.id if latest else None,
                    "attempt_number": latest.attempt_number if latest else None,
                    "answered_count": (
                        presence.get("answered_count", latest.answered_count)
                        if latest
                        else 0
                    ),
                    "time_left_seconds": (
                        presence.get("time_left_seconds", latest.time_left_seconds)
                        if latest
                        else None
                    ),
                    "last_heartbeat_at": (
                        presence.get("last_heartbeat_at", latest.last_heartbeat_at)
                        if latest
                        else None
                    ),
                    "submitted_at": latest.submitted_at if latest else None,
                    "score_toeic": latest.score_toeic if latest else None,
                    "listening_score": latest.listening_score if latest else None,
                    "reading_score": latest.reading_score if latest else None,
                    "correct_count": latest.correct_count if latest else None,
                    "graded_count": latest.graded_count if latest else None,
                    "time_spent_seconds": latest.time_spent_seconds if latest else None,
                    "violation_count": event_counts.get(latest.id, 0) if latest else 0,
                }
            )
        history = []
        member_by_id = {row.id: row for row in members}
        for attempt in history_attempts:
            member = member_by_id.get(str(attempt.class_member_id))
            assignment = assignment_by_id.get(str(attempt.class_assignment_id))
            if member is None or assignment is None:
                continue
            history.append(
                {
                    "attempt_id": attempt.id,
                    "attempt_number": attempt.attempt_number,
                    "assignment_id": assignment.id,
                    "assignment_title": assignment.title,
                    "assignment_mode": assignment.mode,
                    "member_id": member.id,
                    "member_ref": member.id[:6].upper(),
                    "full_name": member.full_name,
                    "state": attempt_state(attempt),
                    "status": attempt.status,
                    "answered_count": attempt.answered_count,
                    "time_left_seconds": attempt.time_left_seconds,
                    "score_toeic": attempt.score_toeic,
                    "listening_score": attempt.listening_score,
                    "reading_score": attempt.reading_score,
                    "correct_count": attempt.correct_count,
                    "graded_count": attempt.graded_count,
                    "time_spent_seconds": attempt.time_spent_seconds,
                    "submit_reason": attempt.submit_reason,
                    "violation_count": event_counts.get(attempt.id, 0),
                    "started_at": attempt.started_at,
                    "submitted_at": attempt.submitted_at,
                    "last_heartbeat_at": attempt.last_heartbeat_at,
                }
            )
        return {
            "items": items,
            "history": history,
            "history_limit": history_limit,
            "history_truncated": int(total_attempts or 0) > len(history),
            "summary": {
                "members": len(members),
                "total_attempts": int(total_attempts or 0),
                "completed_attempts": int(completed_attempts or 0),
                "in_progress": sum(item["state"] == "in_progress" for item in items),
                "disconnected": sum(item["state"] == "disconnected" for item in items),
                "submitted": sum(item["state"] == "submitted" for item in items),
                "average_score": average_score,
                "highest_score": highest_score,
            },
            "server_time": now,
        }


@router.get("/teacher/classrooms/{classroom_id}/members/{member_id}/results")
def class_member_results(
    classroom_id: str, member_id: str, request: Request
) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        _accessible_classroom(session, classroom_id, identity["user_id"])
        member = session.get(ClassMember, member_id)
        if member is None or member.classroom_id != classroom_id:
            raise HTTPException(status_code=404, detail="Không tìm thấy học viên")
        rows = session.execute(
            select(Attempt, ClassAssignment)
            .join(ClassAssignment, ClassAssignment.id == Attempt.class_assignment_id)
            .where(
                Attempt.class_member_id == member.id,
                ClassAssignment.publication_kind.is_(None),
                ClassAssignment.mode == "exam",
            )
            .order_by(Attempt.started_at.desc())
        ).all()
        attempt_ids = [attempt.id for attempt, _ in rows]
        violation_counts = (
            dict(
                session.execute(
                    select(AntiCheatEvent.attempt_id, func.count(AntiCheatEvent.id))
                    .where(AntiCheatEvent.attempt_id.in_(attempt_ids))
                    .group_by(AntiCheatEvent.attempt_id)
                ).all()
            )
            if attempt_ids
            else {}
        )
        return {
            "member": {
                "id": member.id,
                "member_ref": member.id[:6].upper(),
                "full_name": member.full_name,
            },
            "items": [
                {
                    "attempt_id": attempt.id,
                    "attempt_number": attempt.attempt_number,
                    "assignment_id": assignment.id,
                    "title": assignment.title,
                    "mode": assignment.mode,
                    "status": attempt.status,
                    "score_toeic": attempt.score_toeic,
                    "listening_score": attempt.listening_score,
                    "reading_score": attempt.reading_score,
                    "correct_count": attempt.correct_count,
                    "graded_count": attempt.graded_count,
                    "time_spent_seconds": attempt.time_spent_seconds,
                    "started_at": attempt.started_at,
                    "submitted_at": attempt.submitted_at,
                    "violation_count": violation_counts.get(attempt.id, 0),
                }
                for attempt, assignment in rows
            ],
        }


@router.get("/teacher/classrooms/{classroom_id}/attempts/{attempt_id}/events")
def attempt_events(classroom_id: str, attempt_id: str, request: Request) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        _accessible_classroom(session, classroom_id, identity["user_id"])
        attempt = session.get(Attempt, attempt_id)
        assignment = (
            session.get(ClassAssignment, attempt.class_assignment_id) if attempt else None
        )
        if (
            assignment is None
            or assignment.classroom_id != classroom_id
            or _is_study_resource(assignment)
        ):
            raise HTTPException(status_code=404, detail="Không tìm thấy lượt làm bài")
        rows = session.scalars(
            select(AntiCheatEvent)
            .where(AntiCheatEvent.attempt_id == attempt.id)
            .order_by(AntiCheatEvent.received_at)
        ).all()
        return {
            "items": [
                {
                    "id": row.id,
                    "event_type": row.event_type,
                    "occurred_at": row.client_occurred_at,
                    "received_at": row.received_at,
                    "detail": row.detail,
                }
                for row in rows
            ]
        }


@router.post("/classrooms/resolve")
def resolve_classroom(
    body: ClassroomResolveRequest, request: Request
) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail="Luồng tham gia lớp ẩn danh đã ngừng; hãy đăng nhập bằng tài khoản Student",
    )


def _legacy_resolve_classroom(
    body: ClassroomResolveRequest, request: Request
) -> dict[str, Any]:
    code = _normalize_code(body.code)
    _rate_limit_join(request, code)
    with session_scope() as session:
        classroom = session.scalar(
            select(Classroom).where(
                Classroom.join_code == code,
                Classroom.status == "active",
            )
        )
        if classroom is None:
            raise HTTPException(status_code=404, detail="Mã lớp học không hợp lệ")
        return {"id": classroom.id, "name": classroom.name}


@router.post("/classrooms/join")
def join_classroom(body: ClassroomJoinRequest, request: Request) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail="Luồng tham gia lớp ẩn danh đã ngừng; hãy đăng nhập bằng tài khoản Student",
    )


def _legacy_join_classroom(body: ClassroomJoinRequest, request: Request) -> dict[str, Any]:
    code = _normalize_code(body.code)
    _rate_limit_join(request, code, body.browser_key)
    with session_scope() as session:
        classroom = session.scalar(
            select(Classroom)
            .where(
                Classroom.join_code == code,
                Classroom.status == "active",
            )
            .with_for_update()
        )
        if classroom is None:
            raise HTTPException(status_code=404, detail="Mã lớp học không hợp lệ")
        browser_hash = sha256(body.browser_key)
        member = session.scalar(
            select(ClassMember).where(
                ClassMember.classroom_id == classroom.id,
                ClassMember.browser_key_hash == browser_hash,
            )
        )
        if member is None:
            member = ClassMember(
                classroom_id=classroom.id,
                full_name=_clean_name(body.full_name),
                browser_key_hash=browser_hash,
            )
            session.add(member)
            session.flush()
        elif member.status != "active":
            raise HTTPException(
                status_code=403,
                detail="Bạn đã bị loại khỏi lớp học này",
            )
        else:
            member.full_name = _clean_name(body.full_name)
            member.last_seen_at = utcnow()
        return {
            "class_session_token": _issue_class_session(member),
            "classroom": _classroom_payload(session, classroom, include_code=False),
            "member": {
                "id": member.id,
                "member_ref": member.id[:6].upper(),
                "full_name": member.full_name,
            },
        }


@router.get("/class-session/classroom")
def class_session_classroom(request: Request) -> dict[str, Any]:
    with session_scope() as session:
        member, classroom = _class_session(request, session)
        result = _classroom_payload(session, classroom, include_code=False)
        result["member"] = {
            "id": member.id,
            "member_ref": member.id[:6].upper(),
            "full_name": member.full_name,
        }
        return result


@router.get("/class-session/assignments")
def class_session_assignments(request: Request) -> dict[str, Any]:
    with session_scope() as session:
        member, classroom = _class_session(request, session)
        rows = session.execute(
            select(ClassAssignment, ExamVersion)
            .join(ExamVersion, ExamVersion.id == ClassAssignment.exam_version_id)
            .where(
                ClassAssignment.classroom_id == classroom.id,
                ClassAssignment.status == "published",
                or_(
                    ClassAssignment.closes_at.is_(None),
                    ClassAssignment.closes_at > utcnow(),
                ),
            )
            .order_by(ClassAssignment.created_at.desc())
        ).all()
        assignment_ids = [assignment.id for assignment, _ in rows]
        attempts = (
            session.scalars(
                select(Attempt)
                .where(
                    Attempt.class_assignment_id.in_(assignment_ids),
                    Attempt.class_member_id == member.id,
                )
                .order_by(Attempt.started_at.desc())
            ).all()
            if assignment_ids
            else []
        )
        attempt_counts: dict[str, int] = {}
        latest_attempts: dict[str, Attempt] = {}
        for attempt in attempts:
            assignment_id = str(attempt.class_assignment_id)
            attempt_counts[assignment_id] = attempt_counts.get(assignment_id, 0) + 1
            latest_attempts.setdefault(assignment_id, attempt)
        items = []
        now = utcnow()
        for assignment, version in rows:
            attempt_count = attempt_counts.get(assignment.id, 0)
            latest_attempt = latest_attempts.get(assignment.id)
            data = _assignment_payload(assignment, version)
            opens_at = _aware(assignment.opens_at)
            closes_at = _aware(assignment.closes_at)
            data.update(
                {
                    "attempt_count": attempt_count,
                    "attempts_remaining": (
                        max(0, assignment.attempt_limit - attempt_count)
                        if not _is_study_resource(assignment)
                        and assignment.mode != "practice"
                        and assignment.attempt_limit is not None
                        else None
                    ),
                    "availability": (
                        "closed"
                        if assignment.status == "closed"
                        or (closes_at and closes_at <= now)
                        else "upcoming"
                        if opens_at and opens_at > now
                        else "open"
                    ),
                    "score_released": _release_visible(assignment, "score"),
                    "answers_released": _release_visible(assignment, "answer"),
                    "latest_attempt": (
                        {
                            "id": latest_attempt.id,
                            "status": latest_attempt.status,
                            "submitted_at": latest_attempt.submitted_at,
                            "scores": (
                                {
                                    "toeic": latest_attempt.score_toeic,
                                    "listening": latest_attempt.listening_score,
                                    "reading": latest_attempt.reading_score,
                                }
                                if latest_attempt.status == "submitted"
                                and _release_visible(assignment, "score")
                                else None
                            ),
                        }
                        if latest_attempt
                        else None
                    ),
                }
            )
            items.append(data)
        return {"items": items}


@router.get("/teacher/exams/{exam_id}/practice-publications")
@router.get("/teacher/exams/{exam_id}/class-publications")
def list_class_publications(exam_id: str, request: Request) -> dict[str, Any]:
    identity = require_teacher(request)
    with session_scope() as session:
        exam = session.get(Exam, exam_id)
        if (
            exam is None
            or exam.library_scope != "teacher_shared"
            or exam.deleted_at is not None
        ):
            raise HTTPException(status_code=404, detail="Không tìm thấy đề trong kho")
        co_taught_ids = set(
            session.scalars(
                select(ClassroomCoTeacher.classroom_id).where(
                    ClassroomCoTeacher.teacher_user_id == identity["user_id"],
                    ClassroomCoTeacher.status == "active",
                )
            )
        )
        rows = session.execute(
            select(ClassAssignment, Classroom, ExamVersion)
            .join(Classroom, Classroom.id == ClassAssignment.classroom_id)
            .join(ExamVersion, ExamVersion.id == ClassAssignment.exam_version_id)
            .where(
                Classroom.id.in_(
                    {c.id for c in session.scalars(
                        select(Classroom).where(
                            Classroom.owner_teacher_id == identity["user_id"]
                        )
                    )} | co_taught_ids
                ),
                ExamVersion.source_exam_id == exam.id,
                ClassAssignment.publication_kind.in_(
                    list(STUDY_PUBLICATION_KINDS)
                ),
            )
            .order_by(ClassAssignment.created_at.desc())
        ).all()
        return {
            "items": [
                {
                    "classroom_id": classroom.id,
                    "classroom_name": classroom.name,
                    "assignment_id": assignment.id,
                    "status": assignment.status,
                    "kind": "study_resource",
                    "question_count": version.question_count,
                    "available_part_numbers": _available_part_numbers(
                        version.payload or {}
                    ),
                    "content_hash": version.content_hash,
                }
                for assignment, classroom, version in rows
            ]
        }


def _publication_classrooms(
    session: Any, classroom_ids: list[str], teacher_id: str
) -> dict[str, Classroom]:
    classrooms = session.scalars(
        select(Classroom).where(Classroom.id.in_(classroom_ids)).with_for_update()
    ).all()
    by_id = {item.id: item for item in classrooms}
    # Build set of classroom IDs this teacher co-teaches
    co_taught_ids = set(
        session.scalars(
            select(ClassroomCoTeacher.classroom_id).where(
                ClassroomCoTeacher.classroom_id.in_(classroom_ids),
                ClassroomCoTeacher.teacher_user_id == teacher_id,
                ClassroomCoTeacher.status == "active",
            )
        )
    )
    for classroom_id in classroom_ids:
        if classroom_id not in by_id or by_id[classroom_id].status != "active":
            raise HTTPException(
                status_code=422,
                detail="Có lớp không tồn tại hoặc đã lưu trữ",
            )
        is_owner = by_id[classroom_id].owner_teacher_id == teacher_id
        is_co = classroom_id in co_taught_ids
        if not is_owner and not is_co:
            raise HTTPException(
                status_code=422,
                detail="Có lớp không tồn tại, đã lưu trữ hoặc không thuộc giáo viên",
            )
    return by_id


def _publish_exam_in_session(
    session: Any,
    exam: Exam,
    teacher_id: str,
    classroom_ids: list[str],
    classrooms_by_id: dict[str, Classroom],
) -> dict[str, Any]:
    selected_parts = _available_part_numbers(exam.payload or {})
    full_payload = _filtered_publication_payload(exam.payload or {}, selected_parts)
    full_payload["category"] = (exam.category or "").strip()
    duration_seconds = max(60, exam.duration_minutes * 60)
    # Compare against the exact immutable payload used by _snapshot_exam.
    # The snapshot additionally persists the source slug, so hashing only the
    # filtered payload made a repeat publication miss its existing version and
    # attempt to insert the same publication_key a second time.
    snapshot_payload = copy.deepcopy(full_payload)
    snapshot_payload["category"] = (exam.category or "").strip()
    snapshot_payload["slug"] = exam.slug
    encoded = json.dumps(
        snapshot_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    created: list[str] = []
    reopened: list[str] = []
    already_published: list[str] = []
    pending_classrooms: list[Classroom] = []
    for classroom_id in classroom_ids:
        existing_rows = session.execute(
            select(ClassAssignment, ExamVersion)
            .join(ExamVersion, ExamVersion.id == ClassAssignment.exam_version_id)
            .where(
                ClassAssignment.classroom_id == classroom_id,
                ClassAssignment.publication_kind.in_(list(STUDY_PUBLICATION_KINDS)),
                ExamVersion.source_exam_id == exam.id,
            )
            .order_by(ClassAssignment.created_at.desc())
        ).all()
        same = next(
            (
                (assignment, version)
                for assignment, version in existing_rows
                if version.content_hash == content_hash
            ),
            None,
        )
        if same:
            assignment, _ = same
            assignment.mode = "practice"
            assignment.duration_seconds = duration_seconds
            assignment.attempt_limit = None
            assignment.score_release = "immediate"
            assignment.answer_release = "immediate"
            assignment.anti_cheat_enabled = False
            assignment.publication_kind = "study_resource"
            for other_assignment, _ in existing_rows:
                if (
                    other_assignment.id != assignment.id
                    and other_assignment.status == "published"
                ):
                    other_assignment.status = "closed"
                    other_assignment.updated_at = utcnow()
            if assignment.status == "closed":
                assignment.status = "published"
                assignment.closes_at = None
                assignment.published_at = assignment.published_at or utcnow()
                assignment.updated_at = utcnow()
                reopened.append(classroom_id)
            else:
                already_published.append(classroom_id)
            continue
        for assignment, _ in existing_rows:
            if assignment.status == "published":
                assignment.status = "closed"
                assignment.updated_at = utcnow()
        pending_classrooms.append(classrooms_by_id[classroom_id])

    version = (
        _snapshot_exam(
            session,
            exam,
            teacher_id,
            payload_override=full_payload,
            duration_seconds=duration_seconds,
        )
        if pending_classrooms
        else None
    )
    for classroom in pending_classrooms:
        publication_key = sha256(
            f"public:{teacher_id}:{classroom.id}:{exam.id}:"
            f"study_resource:{content_hash}"
        )
        session.add(
            ClassAssignment(
                classroom_id=classroom.id,
                exam_version_id=version.id,
                title=exam.title,
                mode="practice",
                status="published",
                opens_at=None,
                closes_at=None,
                duration_seconds=duration_seconds,
                attempt_limit=None,
                score_release="immediate",
                answer_release="immediate",
                anti_cheat_enabled=False,
                publication_kind="study_resource",
                publication_key=publication_key,
                published_at=utcnow(),
            )
        )
        created.append(classroom.id)
    return {
        "created": created,
        "reopened": reopened,
        "already_published": already_published,
        "kind": "study_resource",
        "question_count": len(full_payload.get("questions", [])),
        "part_numbers": selected_parts,
    }


@router.get("/teacher/exam-tags/class-publications")
def list_tag_class_publications(tag: str, request: Request) -> dict[str, Any]:
    identity = require_teacher(request)
    normalized_tag = " ".join(tag.split())
    if not normalized_tag:
        raise HTTPException(status_code=422, detail="Tag không hợp lệ")
    with session_scope() as session:
        exams = session.scalars(
            select(Exam).where(
                Exam.library_scope == "teacher_shared",
                Exam.deleted_at.is_(None),
                Exam.owner_user_id == identity["user_id"],
                func.lower(func.trim(Exam.category)) == normalized_tag.lower(),
            )
        ).all()
        if not exams:
            raise HTTPException(status_code=404, detail="Tag chưa có đề nào")
        co_taught_ids = set(
            session.scalars(
                select(ClassroomCoTeacher.classroom_id).where(
                    ClassroomCoTeacher.teacher_user_id == identity["user_id"],
                    ClassroomCoTeacher.status == "active",
                )
            )
        )
        classrooms = session.scalars(
            select(Classroom)
            .where(
                Classroom.status == "active",
                Classroom.id.in_(
                    {c.id for c in session.scalars(
                        select(Classroom).where(
                            Classroom.owner_teacher_id == identity["user_id"]
                        )
                    )} | co_taught_ids
                ),
            )
            .order_by(Classroom.created_at.desc())
        ).all()
        exam_ids = {exam.id for exam in exams}
        published_by_classroom: dict[str, set[str]] = {}
        if classrooms:
            rows = session.execute(
                select(ClassAssignment.classroom_id, ExamVersion.source_exam_id)
                .join(ExamVersion, ExamVersion.id == ClassAssignment.exam_version_id)
                .where(
                    ClassAssignment.classroom_id.in_([item.id for item in classrooms]),
                    ClassAssignment.status == "published",
                    ClassAssignment.publication_kind.in_(list(STUDY_PUBLICATION_KINDS)),
                    ExamVersion.source_exam_id.in_(exam_ids),
                )
            ).all()
            for classroom_id, source_exam_id in rows:
                published_by_classroom.setdefault(classroom_id, set()).add(source_exam_id)
        return {
            "tag": normalized_tag,
            "exam_count": len(exams),
            "items": [
                {
                    "classroom_id": classroom.id,
                    "classroom_name": classroom.name,
                    "published_exam_count": len(
                        published_by_classroom.get(classroom.id, set())
                    ),
                    "exam_count": len(exams),
                    "fully_published": published_by_classroom.get(
                        classroom.id, set()
                    ) == exam_ids,
                }
                for classroom in classrooms
            ],
        }


@router.post("/teacher/exam-tags/class-publications")
def publish_tag_to_classrooms(
    body: TagClassPublicationRequest, request: Request
) -> dict[str, Any]:
    identity = require_teacher(request)
    normalized_tag = " ".join(body.tag.split())
    classroom_ids = list(dict.fromkeys(body.classroom_ids))
    with session_scope() as session:
        exams = session.scalars(
            select(Exam)
            .where(
                Exam.library_scope == "teacher_shared",
                Exam.deleted_at.is_(None),
                Exam.owner_user_id == identity["user_id"],
                func.lower(func.trim(Exam.category)) == normalized_tag.lower(),
            )
            .order_by(Exam.created_at, Exam.id)
            .with_for_update()
        ).all()
        if not exams:
            raise HTTPException(status_code=404, detail="Tag chưa có đề nào")
        classrooms_by_id = _publication_classrooms(
            session, classroom_ids, identity["user_id"]
        )
        results = [
            {
                "exam_id": exam.id,
                "title": exam.title,
                **_publish_exam_in_session(
                    session,
                    exam,
                    identity["user_id"],
                    classroom_ids,
                    classrooms_by_id,
                ),
            }
            for exam in exams
        ]
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="classroom.exam_tag_published",
                target_type="exam_tag",
                target_id=None,
                detail={
                    "tag": normalized_tag,
                    "exam_ids": [exam.id for exam in exams],
                    "classroom_ids": classroom_ids,
                },
            )
        )
        return {
            "tag": normalized_tag,
            "exam_count": len(exams),
            "classroom_ids": classroom_ids,
            "created_count": sum(len(item["created"]) for item in results),
            "reopened_count": sum(len(item["reopened"]) for item in results),
            "already_published_count": sum(
                len(item["already_published"]) for item in results
            ),
            "results": results,
        }


@router.post("/teacher/exams/{exam_id}/practice-publications")
@router.post("/teacher/exams/{exam_id}/class-publications")
def publish_exam_to_classrooms(
    exam_id: str, body: ClassPublicationRequest, request: Request
) -> dict[str, Any]:
    identity = require_teacher(request)
    classroom_ids = list(dict.fromkeys(body.classroom_ids))
    with session_scope() as session:
        exam = session.scalar(select(Exam).where(Exam.id == exam_id).with_for_update())
        if (
            exam is None
            or exam.library_scope != "teacher_shared"
            or exam.deleted_at is not None
            or exam.owner_user_id != identity["user_id"]
        ):
            raise HTTPException(status_code=404, detail="Không tìm thấy đề trong kho")
        classrooms_by_id = _publication_classrooms(
            session, classroom_ids, identity["user_id"]
        )
        result = _publish_exam_in_session(
            session,
            exam,
            identity["user_id"],
            classroom_ids,
            classrooms_by_id,
        )
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="classroom.bank_exam_published",
                target_type="exam",
                target_id=exam.id,
                detail={
                    "created": result["created"],
                    "reopened": result["reopened"],
                    "already_published": result["already_published"],
                    "kind": "study_resource",
                    "part_numbers": result["part_numbers"],
                },
            )
        )
        return {
            key: value
            for key, value in result.items()
            if key != "part_numbers"
        }


def _student_member(
    session: Any, request: Request, classroom_id: str, *, active: bool = True
) -> tuple[dict[str, Any], ClassMember, Classroom]:
    identity = require_roles(request, "student")
    member = session.scalar(
        select(ClassMember).where(
            ClassMember.classroom_id == classroom_id,
            ClassMember.user_id == identity["user_id"],
        )
    )
    classroom = session.get(Classroom, classroom_id)
    if member is None or classroom is None:
        raise HTTPException(status_code=404, detail="Bạn chưa tham gia lớp học này")
    if active and member.status != "active":
        raise HTTPException(status_code=403, detail="Bạn không còn trong lớp học này")
    now = utcnow()
    last_seen = _aware(member.last_seen_at)
    if (
        last_seen is None
        or now - last_seen
        >= timedelta(seconds=max(10, settings.presence_write_interval_seconds))
    ):
        member.last_seen_at = now
    return identity, member, classroom


def _inject_class_session(request: Request, member: ClassMember) -> None:
    headers = [
        (key, value)
        for key, value in request.scope.get("headers", [])
        if key.lower() != b"x-classroom-session"
    ]
    headers.append((b"x-classroom-session", _issue_class_session(member).encode()))
    request.scope["headers"] = headers
    if hasattr(request, "_headers"):
        delattr(request, "_headers")


@router.get("/student/classrooms")
def list_student_classrooms(request: Request) -> dict[str, Any]:
    identity = require_roles(request, "student")
    with session_scope() as session:
        rows = session.execute(
            select(ClassMember, Classroom)
            .join(Classroom, Classroom.id == ClassMember.classroom_id)
            .where(ClassMember.user_id == identity["user_id"])
            .order_by(ClassMember.last_seen_at.desc())
        ).all()
        member_counts, assignment_counts = _classroom_counts(
            session, [classroom.id for _, classroom in rows]
        )
        return {
            "items": [
                {
                    **_classroom_payload(
                        session,
                        classroom,
                        include_code=False,
                        member_count=member_counts.get(classroom.id, 0),
                        assignment_count=assignment_counts.get(classroom.id, 0),
                    ),
                    "membership_status": member.status,
                    "member": {
                        "id": member.id,
                        "member_ref": member.id[:6].upper(),
                        "full_name": member.full_name,
                    },
                }
                for member, classroom in rows
            ]
        }


@router.post("/student/classrooms/resolve")
def resolve_student_classroom(
    body: ClassroomResolveRequest, request: Request
) -> dict[str, Any]:
    identity = require_roles(request, "student")
    code = _normalize_code(body.code)
    _rate_limit_join(request, code, trusted_subject=identity["user_id"])
    with session_scope() as session:
        classroom = session.scalar(
            select(Classroom).where(
                Classroom.join_code == code,
                Classroom.status == "active",
            )
        )
        if classroom is None:
            raise HTTPException(status_code=404, detail="Mã lớp học không hợp lệ")
        return {"id": classroom.id, "name": classroom.name}


@router.post("/student/classrooms/join")
def join_student_classroom(
    body: StudentJoinRequest, request: Request
) -> dict[str, Any]:
    identity = require_roles(request, "student")
    code = _normalize_code(body.code)
    _rate_limit_join(request, code, trusted_subject=identity["user_id"])
    with session_scope() as session:
        classroom = session.scalar(
            select(Classroom)
            .where(
                Classroom.join_code == code,
                Classroom.status == "active",
            )
            .with_for_update()
        )
        if classroom is None:
            raise HTTPException(status_code=404, detail="Mã lớp học không hợp lệ")
        member = session.scalar(
            select(ClassMember).where(
                ClassMember.classroom_id == classroom.id,
                ClassMember.user_id == identity["user_id"],
            )
        )
        if member is None and body.legacy_session_token:
            try:
                claims = jwt.decode(
                    body.legacy_session_token,
                    settings.jwt_secret,
                    algorithms=["HS256"],
                )
            except jwt.PyJWTError:
                claims = {}
            legacy = session.get(ClassMember, claims.get("sub"))
            if (
                claims.get("type") == "class_session"
                and legacy
                and legacy.classroom_id == classroom.id
                and legacy.user_id in {None, identity["user_id"]}
            ):
                member = legacy
                member.user_id = identity["user_id"]
        if member is None:
            member = ClassMember(
                classroom_id=classroom.id,
                user_id=identity["user_id"],
                full_name=identity["display_name"],
                browser_key_hash=None,
            )
            session.add(member)
            session.flush()
        elif member.status != "active":
            raise HTTPException(status_code=403, detail="Bạn đã bị loại khỏi lớp học này")
        else:
            member.full_name = identity["display_name"]
            member.last_seen_at = utcnow()
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="classroom.student_joined",
                target_type="class_member",
                target_id=member.id,
                detail={"classroom_id": classroom.id},
            )
        )
        return {
            "classroom": _classroom_payload(session, classroom, include_code=False),
            "member": {
                "id": member.id,
                "member_ref": member.id[:6].upper(),
                "full_name": member.full_name,
            },
        }


@router.get("/student/classrooms/{classroom_id}")
def get_student_classroom(classroom_id: str, request: Request) -> dict[str, Any]:
    with session_scope() as session:
        _, member, classroom = _student_member(session, request, classroom_id)
        result = _classroom_payload(session, classroom, include_code=False)
        result["member"] = {
            "id": member.id,
            "member_ref": member.id[:6].upper(),
            "full_name": member.full_name,
        }
        return result


@router.get("/student/classrooms/{classroom_id}/assignments")
def list_student_assignments(classroom_id: str, request: Request) -> dict[str, Any]:
    request.state.student_classroom_id = classroom_id
    return class_session_assignments(request)


def _student_member_for_assignment(
    request: Request, assignment_id: str
) -> ClassMember:
    with session_scope() as session:
        assignment = session.get(ClassAssignment, assignment_id)
        if assignment is None:
            raise HTTPException(status_code=404, detail="Bài thi không khả dụng")
        _, member, _ = _student_member(
            session, request, assignment.classroom_id
        )
        session.expunge(member)
        return member


@router.post("/student/assignments/{assignment_id}/attempts")
def start_student_attempt(
    assignment_id: str,
    request: Request,
    body: StudentAttemptStartRequest | None = None,
) -> dict[str, Any]:
    request.state.student_assignment_id = assignment_id
    return start_class_attempt(assignment_id, request, body)


@router.post("/student/assignments/{assignment_id}/offline-pack")
def create_student_offline_pack(
    assignment_id: str,
    request: Request,
    body: StudentAttemptStartRequest | None = None,
) -> dict[str, Any]:
    """Reserve an authoritative attempt while online for later offline work."""
    request.state.student_assignment_id = assignment_id
    payload = start_class_attempt(assignment_id, request, body)
    return {
        **payload,
        "offline_pack": True,
        "offline_pack_expires_in": payload.get("time_left_seconds", 0),
    }


@router.get("/student/attempts/{attempt_id}")
def get_student_attempt(attempt_id: str, request: Request) -> dict[str, Any]:
    request.state.student_attempt_id = attempt_id
    return get_class_attempt(attempt_id, request)


@router.get("/student/attempts/{attempt_id}/state")
def get_student_attempt_state(attempt_id: str, request: Request) -> dict[str, Any]:
    request.state.student_attempt_id = attempt_id
    return get_class_attempt_state(attempt_id, request)


@router.patch("/student/attempts/{attempt_id}/answers")
def save_student_attempt_answers(
    attempt_id: str, body: AttemptAnswersRequest, request: Request
) -> dict[str, Any]:
    request.state.student_attempt_id = attempt_id
    return save_class_attempt_answers(attempt_id, body, request)


@router.patch("/student/attempts/{attempt_id}/sync")
def sync_student_attempt(
    attempt_id: str, body: AttemptSyncRequest, request: Request
) -> dict[str, Any]:
    request.state.student_attempt_id = attempt_id
    return sync_class_attempt(attempt_id, body, request)


@router.post("/student/attempts/{attempt_id}/heartbeat")
def student_attempt_heartbeat(
    attempt_id: str, body: HeartbeatRequest, request: Request
) -> dict[str, Any]:
    request.state.student_attempt_id = attempt_id
    return class_attempt_heartbeat(attempt_id, body, request)


@router.post("/student/attempts/{attempt_id}/events")
def student_attempt_events(
    attempt_id: str, body: AntiCheatEventsRequest, request: Request
) -> dict[str, Any]:
    request.state.student_attempt_id = attempt_id
    return class_attempt_events(attempt_id, body, request)


@router.post("/student/attempts/{attempt_id}/submit")
def submit_student_attempt(
    attempt_id: str, body: AttemptAnswersRequest, request: Request
) -> dict[str, Any]:
    request.state.student_attempt_id = attempt_id
    return submit_class_attempt(attempt_id, body, request)


@router.get("/student/attempts/{attempt_id}/result")
def student_attempt_result(attempt_id: str, request: Request) -> dict[str, Any]:
    identity = require_roles(request, "student")
    with session_scope() as session:
        attempt = session.get(Attempt, attempt_id)
        member = session.get(ClassMember, attempt.class_member_id) if attempt else None
        if attempt is None or member is None or member.user_id != identity["user_id"]:
            raise HTTPException(status_code=404, detail="Không tìm thấy lượt làm bài")
        owned_attempt, assignment, version = _owned_class_attempt(
            session, attempt_id, member
        )
        if owned_attempt.status != "submitted":
            raise HTTPException(status_code=409, detail="Bài thi chưa được nộp")
        # Submitted history remains readable after a teacher removes a student
        # or archives the classroom; release policies still apply.
        return _attempt_result(session, owned_attempt, assignment, version)


@router.get("/student/history")
def student_attempt_history(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=256),
) -> dict[str, Any]:
    identity = require_roles(request, "student")
    with session_scope() as session:
        filters = (
            ClassMember.user_id == identity["user_id"],
            Attempt.status == "submitted",
        )
        total = int(
            session.scalar(
                select(func.count(Attempt.id))
                .join(ClassMember, ClassMember.id == Attempt.class_member_id)
                .where(*filters)
            )
            or 0
        )
        row_query = (
            select(Attempt, ClassAssignment, Classroom, ExamVersion)
            .join(ClassMember, ClassMember.id == Attempt.class_member_id)
            .join(ClassAssignment, ClassAssignment.id == Attempt.class_assignment_id)
            .join(Classroom, Classroom.id == ClassAssignment.classroom_id)
            .join(ExamVersion, ExamVersion.id == ClassAssignment.exam_version_id)
            .where(*filters)
        )
        if cursor:
            try:
                cursor_time, cursor_id = decode_submitted_cursor(cursor)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            row_query = row_query.where(
                or_(
                    Attempt.submitted_at < cursor_time,
                    and_(
                        Attempt.submitted_at == cursor_time,
                        Attempt.id < cursor_id,
                    ),
                )
            )
        else:
            row_query = row_query.offset((page - 1) * page_size)
        fetched_rows = session.execute(
            row_query
            .order_by(Attempt.submitted_at.desc(), Attempt.id.desc())
            .limit(page_size + 1)
        ).all()
        has_more = len(fetched_rows) > page_size
        rows = fetched_rows[:page_size]
        last_attempt = rows[-1][0] if rows else None
        return {
            "items": [
                {
                    "id": attempt.id,
                    "client_exam_id": attempt.exam_id,
                    "exam_title": assignment.title,
                    "exam_type": "classroom",
                    "classroom_name": classroom.name,
                    "assignment_mode": assignment.mode,
                    "kind": (
                        "study_resource"
                        if _is_study_resource(assignment)
                        else "official_exam"
                    ),
                    "launch_mode": attempt.launch_mode
                    or (
                        "practice"
                        if _is_study_resource(assignment)
                        else "official_exam"
                    ),
                    "attempt_number": attempt.attempt_number,
                    "score_toeic": attempt.score_toeic if _release_visible(assignment, "score") else None,
                    "listening_score": attempt.listening_score if _release_visible(assignment, "score") else None,
                    "reading_score": attempt.reading_score if _release_visible(assignment, "score") else None,
                    "correct_count": attempt.correct_count if _release_visible(assignment, "score") else None,
                    "total_questions": attempt.graded_count,
                    "duration_seconds": attempt.duration_seconds,
                    "time_spent_seconds": attempt.time_spent_seconds or 0,
                    "mode": (
                        "exam"
                        if (attempt.launch_mode or "")
                        in {"mock_exam", "official_exam"}
                        else "practice"
                    ),
                    "submitted_at": attempt.submitted_at,
                    "score_released": _release_visible(assignment, "score"),
                    "answers_released": _release_visible(assignment, "answer"),
                    "has_solutions": bool((version.payload or {}).get("solutions")),
                    "source": "classroom",
                }
                for attempt, assignment, classroom, version in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "has_more": has_more,
            "next_cursor": (
                encode_submitted_cursor(last_attempt.submitted_at, last_attempt.id)
                if has_more and last_attempt and last_attempt.submitted_at
                else None
            ),
        }


@router.post("/class-session/assignments/{assignment_id}/attempts")
def start_class_attempt(
    assignment_id: str,
    request: Request,
    body: StudentAttemptStartRequest | None = None,
) -> dict[str, Any]:
    try:
        with session_scope() as session:
            member, classroom = _class_session(request, session)
            # Serialize duplicate starts for one student without serializing
            # every student on the shared assignment row.
            member = session.get(ClassMember, member.id, with_for_update=True)
            if member is None:
                raise HTTPException(status_code=404, detail="Không tìm thấy học viên")
            if classroom.status != "active":
                raise HTTPException(status_code=409, detail="Lớp đang lưu trữ")
            assignment = session.get(ClassAssignment, assignment_id)
            if (
                assignment is None
                or assignment.classroom_id != classroom.id
                or assignment.status != "published"
            ):
                raise HTTPException(status_code=404, detail="Bài thi không khả dụng")
            now = utcnow()
            opens_at = _aware(assignment.opens_at)
            closes_at = _aware(assignment.closes_at)
            if opens_at and now < opens_at:
                raise HTTPException(status_code=409, detail="Bài thi chưa mở")
            if closes_at and now >= closes_at:
                raise HTTPException(status_code=409, detail="Bài thi đã đóng")
            attempt_total, max_attempt_number = session.execute(
                select(
                    func.count(Attempt.id),
                    func.max(Attempt.attempt_number),
                )
                .where(
                    Attempt.class_assignment_id == assignment.id,
                    Attempt.class_member_id == member.id,
                )
            ).one()
            in_progress = session.scalar(
                select(Attempt)
                .where(
                    Attempt.class_assignment_id == assignment.id,
                    Attempt.class_member_id == member.id,
                    Attempt.status == "in_progress",
                )
                .order_by(Attempt.attempt_number.desc())
                .limit(1)
            )
            version = session.get(ExamVersion, assignment.exam_version_id)
            if version is None:
                raise HTTPException(status_code=409, detail="Phiên bản đề không khả dụng")
            study_resource = _is_study_resource(assignment)
            configuration = body or StudentAttemptStartRequest()
            if not study_resource and any(
                value is not None
                for value in (
                    configuration.launch_mode,
                    configuration.part_numbers,
                    configuration.duration_seconds,
                )
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Bài thi chính thức sử dụng cấu hình của giáo viên",
                )
            available_parts = _available_part_numbers(version.payload or {})
            if study_resource:
                launch_mode = configuration.launch_mode or "practice"
                if launch_mode == "practice":
                    selected_parts = list(
                        dict.fromkeys(configuration.part_numbers or available_parts)
                    )
                    if not selected_parts or not set(selected_parts).issubset(
                        set(available_parts)
                    ):
                        raise HTTPException(
                            status_code=422,
                            detail="Part đã chọn không có trong bộ đề",
                        )
                    duration_seconds = (
                        configuration.duration_seconds or assignment.duration_seconds
                    )
                else:
                    selected_parts = available_parts
                    duration_seconds = max(60, version.duration_minutes * 60)
            else:
                launch_mode = "official_exam"
                selected_parts = None
                duration_seconds = assignment.duration_seconds
            if (
                in_progress
                and _aware(in_progress.deadline_at)
                and now >= _aware(in_progress.deadline_at)
            ):
                _finalize_attempt(
                    session, in_progress, assignment, version, reason="timeout"
                )
                in_progress = None
            if in_progress:
                return {
                    "attempt_id": in_progress.id,
                    "exam": _exam_for_student(
                        session,
                        version,
                        assignment=assignment,
                        member_id=member.id,
                        attempt=in_progress,
                    ),
                    "answers": {
                        item.question_number: item.selected
                        for item in session.scalars(
                            select(AttemptAnswer).where(
                                AttemptAnswer.attempt_id == in_progress.id
                            )
                        )
                    },
                    "duration_seconds": in_progress.duration_seconds,
                    "time_left_seconds": in_progress.time_left_seconds,
                    "current_question_number": in_progress.current_question_number,
                    "anti_cheat_enabled": (
                        False if study_resource else assignment.anti_cheat_enabled
                    ),
                    "listening_navigation_locked": (
                        False if study_resource else assignment.listening_navigation_locked
                    ),
                    "launch_mode": in_progress.launch_mode or launch_mode,
                    "selected_part_numbers": in_progress.selected_part_numbers
                    or available_parts,
                    "accepted_revision": in_progress.answer_revision,
                    "exam_content_hash": version.content_hash,
                    "deadline_at": in_progress.deadline_at,
                    "resumed": True,
                }
            if (
                not study_resource
                and assignment.mode != "practice"
                and
                assignment.attempt_limit is not None
                and int(attempt_total or 0) >= assignment.attempt_limit
            ):
                raise HTTPException(status_code=409, detail="Bạn đã hết lượt làm bài")
            if version.source_exam_id is None:
                raise HTTPException(status_code=409, detail="Đề gốc không còn khả dụng")
            deadline = now + timedelta(seconds=duration_seconds)
            if closes_at:
                deadline = min(deadline, closes_at)
            duration = max(1, int((deadline - now).total_seconds()))
            attempt = Attempt(
                exam_id=version.source_exam_id,
                exam_version_id=version.id,
                user_id=member.user_id,
                class_assignment_id=assignment.id,
                class_member_id=member.id,
                attempt_number=int(max_attempt_number or 0) + 1,
                launch_mode=launch_mode,
                selected_part_numbers=selected_parts,
                duration_seconds=duration,
                time_left_seconds=duration,
                deadline_at=deadline,
                last_heartbeat_at=now,
            )
            session.add(attempt)
            session.flush()
            return {
                "attempt_id": attempt.id,
                "exam": _exam_for_student(
                    session,
                    version,
                    assignment=assignment,
                    member_id=member.id,
                    attempt=attempt,
                ),
                "answers": {},
                "duration_seconds": duration,
                "time_left_seconds": duration,
                "current_question_number": None,
                "anti_cheat_enabled": (
                    False if study_resource else assignment.anti_cheat_enabled
                ),
                "listening_navigation_locked": (
                    False if study_resource else assignment.listening_navigation_locked
                ),
                "launch_mode": launch_mode,
                "selected_part_numbers": selected_parts or available_parts,
                "accepted_revision": attempt.answer_revision,
                "exam_content_hash": version.content_hash,
                "deadline_at": attempt.deadline_at,
                "resumed": False,
            }
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail="Lượt làm bài đã được tạo ở yêu cầu khác"
        ) from exc


@router.get("/class-session/attempts/{attempt_id}")
def get_class_attempt(attempt_id: str, request: Request) -> dict[str, Any]:
    with session_scope() as session:
        member, _, attempt, assignment, version = _attempt_session_context(
            request, session, attempt_id
        )
        if (
            attempt.status == "in_progress"
            and _aware(attempt.deadline_at)
            and utcnow() >= _aware(attempt.deadline_at)
        ):
            _finalize_attempt(session, attempt, assignment, version, reason="timeout")
        if attempt.status == "submitted":
            return _attempt_result(session, attempt, assignment, version)
        return {
            "attempt_id": attempt.id,
            "status": attempt.status,
            "exam": _exam_for_student(
                session,
                version,
                assignment=assignment,
                member_id=member.id,
                attempt=attempt,
            ),
            "answers": {
                item.question_number: item.selected
                for item in session.scalars(
                    select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id)
                )
            },
            "duration_seconds": attempt.duration_seconds,
            "time_left_seconds": attempt.time_left_seconds,
            "current_question_number": attempt.current_question_number,
            "accepted_revision": attempt.answer_revision,
            "deadline_at": attempt.deadline_at,
            "anti_cheat_enabled": (
                False if _is_study_resource(assignment) else assignment.anti_cheat_enabled
            ),
            "listening_navigation_locked": (
                False
                if _is_study_resource(assignment)
                else assignment.listening_navigation_locked
            ),
            "launch_mode": attempt.launch_mode
            or ("practice" if _is_study_resource(assignment) else "official_exam"),
            "selected_part_numbers": attempt.selected_part_numbers
            or _available_part_numbers(version.payload or {}),
        }


@router.get("/class-session/attempts/{attempt_id}/state")
def get_class_attempt_state(attempt_id: str, request: Request) -> dict[str, Any]:
    with session_scope() as session:
        _, _, attempt, assignment, version = _attempt_session_context(
            request, session, attempt_id
        )
        if (
            attempt.status == "in_progress"
            and _aware(attempt.deadline_at)
            and utcnow() >= _aware(attempt.deadline_at)
        ):
            _finalize_attempt(session, attempt, assignment, version, reason="timeout")
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


@router.patch("/class-session/attempts/{attempt_id}/answers")
def save_class_attempt_answers(
    attempt_id: str, body: AttemptAnswersRequest, request: Request
) -> dict[str, Any]:
    with session_scope() as session:
        _, _, attempt, assignment, version = _attempt_session_context(
            request, session, attempt_id, lock=True
        )
        if attempt.status != "in_progress":
            return {
                "ok": True,
                "status": attempt.status,
                "accepted_revision": attempt.answer_revision,
            }
        if _aware(attempt.deadline_at) and utcnow() >= _aware(attempt.deadline_at):
            _finalize_attempt(session, attempt, assignment, version, reason="timeout")
            return {
                "ok": True,
                "status": attempt.status,
                "accepted_revision": attempt.answer_revision,
            }
        _store_answers(session, attempt, version, body)
        return {
            "ok": True,
            "status": attempt.status,
            "accepted_revision": attempt.answer_revision,
        }


@router.patch("/class-session/attempts/{attempt_id}/sync")
def sync_class_attempt(
    attempt_id: str, body: AttemptSyncRequest, request: Request
) -> dict[str, Any]:
    with session_scope() as session:
        _, _, attempt, assignment, version = _attempt_session_context(
            request, session, attempt_id, lock=True
        )
        if (
            attempt.status == "in_progress"
            and _aware(attempt.deadline_at)
            and utcnow() >= _aware(attempt.deadline_at)
        ):
            _finalize_attempt(session, attempt, assignment, version, reason="timeout")
        if attempt.status != "in_progress":
            return {
                "accepted_revision": attempt.answer_revision,
                "accepted_batch_id": str(body.batch_id),
                "server_time": utcnow(),
                "deadline_at": attempt.deadline_at,
                "status": attempt.status,
                "receipt_id": attempt.submit_receipt_id,
            }
        try:
            result = sync_attempt_changes(
                session,
                attempt,
                batch_id=str(body.batch_id),
                base_revision=body.base_revision,
                raw_changes=body.changes,
                allowed_numbers=_allowed_version_question_numbers(
                    session, version, attempt, body.changes
                ),
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
            _presence_payload(
                attempt,
                answered_count=attempt.answered_count,
                current_question_number=body.presence.current_question_number,
                time_left_seconds=attempt.time_left_seconds,
                is_fullscreen=body.presence.is_fullscreen,
                visibility_state=body.presence.visibility_state,
            ),
        )
        return {
            "accepted_revision": result.accepted_revision,
            "accepted_batch_id": result.accepted_batch_id,
            "duplicate": result.duplicate,
            "server_time": utcnow(),
            "deadline_at": attempt.deadline_at,
            "status": attempt.status,
        }


@router.post("/class-session/attempts/{attempt_id}/heartbeat")
def class_attempt_heartbeat(
    attempt_id: str, body: HeartbeatRequest, request: Request
) -> dict[str, Any]:
    live_payload: dict[str, Any] | None = None
    status = "in_progress"
    with session_scope() as session:
        _, _, attempt, assignment, version = _attempt_session_context(
            request, session, attempt_id
        )
        if attempt.status == "in_progress" and _aware(attempt.deadline_at) and utcnow() >= _aware(attempt.deadline_at):
            _finalize_attempt(session, attempt, assignment, version, reason="timeout")
        if attempt.status != "in_progress":
            return {"ok": True, "status": attempt.status}
        status = attempt.status
        time_left_seconds = min(
            attempt.time_left_seconds,
            body.time_left_seconds,
            attempt.duration_seconds,
        )
        live_payload = _presence_payload(
            attempt,
            answered_count=body.answered_count,
            current_question_number=body.current_question_number,
            time_left_seconds=time_left_seconds,
            is_fullscreen=body.is_fullscreen,
            visibility_state=body.visibility_state,
        )
        now = utcnow()
        last_checkpoint = _aware(attempt.last_heartbeat_at)
        if (
            last_checkpoint is None
            or now - last_checkpoint
            >= timedelta(seconds=max(10, settings.presence_write_interval_seconds))
        ):
            attempt.answered_count = body.answered_count
            attempt.current_question_number = body.current_question_number
            attempt.time_left_seconds = time_left_seconds
            attempt.is_fullscreen = body.is_fullscreen
            attempt.visibility_state = body.visibility_state
            attempt.last_heartbeat_at = now
    if live_payload is not None:
        presence_store.put(attempt_id, live_payload)
    return {"ok": True, "status": status, "server_time": utcnow()}


@router.post("/class-session/attempts/{attempt_id}/events")
def class_attempt_events(
    attempt_id: str, body: AntiCheatEventsRequest, request: Request
) -> dict[str, Any]:
    with session_scope() as session:
        _, _, attempt, assignment, _ = _attempt_session_context(
            request, session, attempt_id
        )
        if not assignment.anti_cheat_enabled:
            return {"accepted": 0}
        values: list[dict[str, Any]] = []
        for event in body.events:
            if event.event_type not in CLASS_EVENT_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail=f"Sự kiện {event.event_type} không được hỗ trợ",
                )
            safe_detail = {
                str(key)[:40]: value
                for key, value in list(event.detail.items())[:10]
                if isinstance(value, (str, int, float, bool, type(None)))
            }
            values.append(
                {
                    "id": uuid4(),
                    "attempt_id": attempt.id,
                    "client_event_id": event.client_event_id,
                    "event_type": event.event_type,
                    "client_occurred_at": _aware(event.occurred_at),
                    "received_at": utcnow(),
                    "detail": safe_detail,
                }
            )
        if not values:
            return {"accepted": 0}
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            statement = postgresql_insert(AntiCheatEvent).values(values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["attempt_id", "client_event_id"]
            )
        elif dialect == "sqlite":
            statement = sqlite_insert(AntiCheatEvent).values(values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["attempt_id", "client_event_id"]
            )
        else:
            statement = AntiCheatEvent.__table__.insert().values(values)
        result = session.execute(statement)
        accepted = max(0, int(result.rowcount or 0))
        return {"accepted": accepted}


@router.post("/class-session/attempts/{attempt_id}/submit")
def submit_class_attempt(
    attempt_id: str, body: AttemptAnswersRequest, request: Request
) -> dict[str, Any]:
    with session_scope() as session:
        _, _, attempt, assignment, version = _attempt_session_context(
            request, session, attempt_id, lock=True
        )
        submitted_answers: dict[int, str] | None = None
        question_rows: list[tuple[int, int | None, str | None]] | None = None
        if attempt.status == "in_progress":
            expired = bool(
                _aware(attempt.deadline_at)
                and utcnow() >= _aware(attempt.deadline_at)
            )
            if not expired:
                question_rows = _version_question_rows(session, version, attempt)
                submitted_answers = _store_answers(
                    session,
                    attempt,
                    version,
                    body,
                    force=True,
                    question_rows=question_rows,
                    grade=True,
                )
                idempotency_key = request.headers.get("Idempotency-Key", "").strip()
                if len(idempotency_key) > 80:
                    raise HTTPException(
                        status_code=422, detail="Idempotency-Key quá dài"
                    )
                attempt.submit_idempotency_key = idempotency_key or None
            reason = "timeout" if expired else "submitted"
            submitted_answers = _finalize_attempt(
                session,
                attempt,
                assignment,
                version,
                reason=reason,
                question_rows=question_rows,
                submitted_answers=submitted_answers,
            )
        return _attempt_receipt(
            session,
            attempt,
            assignment,
            version,
            submitted_answers=submitted_answers,
        )


@router.get("/class-session/attempts/{attempt_id}/result")
def class_attempt_result(attempt_id: str, request: Request) -> dict[str, Any]:
    with session_scope() as session:
        _, _, attempt, assignment, version = _attempt_session_context(
            request, session, attempt_id
        )
        if attempt.status != "submitted":
            raise HTTPException(status_code=409, detail="Bài thi chưa được nộp")
        return _attempt_result(session, attempt, assignment, version)


def finalize_expired_class_attempts(limit: int = 500) -> int:
    """Finalize overdue attempts in short, independently committed batches."""

    remaining = max(0, min(limit, 500))
    finalized = 0
    while remaining:
        batch_size = min(25, remaining)
        with session_scope() as session:
            rows = session.execute(
                select(Attempt, ClassAssignment, ExamVersion)
                .join(
                    ClassAssignment,
                    ClassAssignment.id == Attempt.class_assignment_id,
                )
                .join(
                    ExamVersion,
                    ExamVersion.id == ClassAssignment.exam_version_id,
                )
                .where(
                    Attempt.status == "in_progress",
                    Attempt.class_assignment_id.is_not(None),
                    Attempt.deadline_at <= utcnow(),
                )
                .with_for_update(of=Attempt, skip_locked=True)
                .limit(batch_size)
            ).all()
            if not rows:
                break
            for attempt, assignment, version in rows:
                _finalize_attempt(
                    session, attempt, assignment, version, reason="timeout"
                )
                finalized += 1
            remaining -= len(rows)
    return finalized
