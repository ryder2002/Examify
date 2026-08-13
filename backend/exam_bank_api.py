"""Shared Teacher exam bank and immutable self-study attempt APIs."""

from __future__ import annotations

import copy
from datetime import timedelta
import hashlib
from pathlib import Path
import re
import shutil
from typing import Any, Literal
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from auth_service import current_identity, require_roles
from database import session_scope
from config import settings
from exam_bank_scope import exam_bank_visibility_filters, teacher_scoped_title_key
from exam_solutions import normalized_name_key
from models import (
    Attempt,
    AttemptAnswer,
    AuditLog,
    Exam,
    ExamEditSession,
    ExamSource,
    ExamTag,
    ExamVersion,
    ExamVersionAsset,
    Job,
    PublicExamShare,
    SolutionImport,
    SystemState,
    User,
    utcnow,
)
from object_storage import storage


router = APIRouter(prefix="/api/v1")
v2_router = APIRouter(prefix="/api/v2")


class ExamBankPatch(BaseModel):
    base_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    tag_id: str | None = None
    archived: bool | None = None


class ExamBankAttemptCreate(BaseModel):
    launch_mode: Literal["practice", "mock_exam"] = "practice"
    part_numbers: list[int] | None = Field(default=None, max_length=7)
    duration_seconds: int | None = Field(default=None, ge=60, le=4 * 3600)

    @field_validator("part_numbers")
    @classmethod
    def validate_parts(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        normalized = sorted(set(value))
        if len(normalized) != len(value) or any(part < 1 or part > 7 for part in normalized):
            raise ValueError("Part phải duy nhất và nằm trong khoảng 1–7")
        return normalized


class ExamTagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ExamTagUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class EditSessionFinalize(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    tag_id: str | None = None
    tag: str | None = Field(default=None, max_length=120)


def _identity(request: Request) -> dict[str, Any]:
    return require_roles(request, "teacher", "student", "admin")


def _shared_exam(
    session: Any,
    exam_id: str,
    identity: dict[str, Any],
    *,
    lock: bool = False,
) -> Exam:
    statement = select(Exam).where(
        Exam.id == exam_id,
        Exam.library_scope == "teacher_shared",
        Exam.deleted_at.is_(None),
        *exam_bank_visibility_filters(identity),
    )
    if lock:
        statement = statement.with_for_update()
    exam = session.scalar(statement)
    if exam is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề trong Kho chung")
    return exam


def _require_manager(identity: dict[str, Any]) -> None:
    if identity["role"] not in {"teacher", "admin"}:
        raise HTTPException(status_code=403, detail="Chỉ Teacher/Admin được quản trị Kho đề thi")


def _component_payload(payload: dict[str, Any], component: str) -> dict[str, Any]:
    if component == "listening":
        minimum, maximum = 1, 100
    elif component == "reading":
        minimum, maximum = 101, 200
    else:
        minimum, maximum = 1, 200
    questions = [
        copy.deepcopy(question)
        for question in payload.get("questions", [])
        if minimum <= int(question.get("number", 0)) <= maximum
    ]
    selected_numbers = {int(question["number"]) for question in questions}
    stimuli = []
    for source in payload.get("stimuli", []):
        numbers = {
            int(number) for number in source.get("question_numbers", [])
        }
        if numbers.intersection(selected_numbers):
            stimuli.append(copy.deepcopy(source))
    solutions = [
        copy.deepcopy(entry)
        for entry in payload.get("solutions", [])
        if set(int(number) for number in entry.get("question_numbers", [])).issubset(
            selected_numbers
        )
    ]
    audios = copy.deepcopy(payload.get("audios", [])) if component != "reading" else []
    return {
        "questions": questions,
        "stimuli": stimuli,
        "solutions": solutions,
        "audios": audios,
        "audio": next((audio for audio in audios if audio.get("part") == "full"), None),
    }


def _clone_job(
    *,
    source_job_id: str,
    component: str,
    editor_user_id: str,
    exam_id: str,
    revision: int,
    payload: dict[str, Any],
    source_bucket: str | None = None,
    source_object_key: str | None = None,
    version_assets: list[dict[str, Any]] | None = None,
) -> str:
    from job_store import store as job_store

    valid_source_job = bool(
        re.fullmatch(r"[0-9a-fA-F-]{36}", source_job_id or "")
    )
    if not valid_source_job and not source_object_key:
        raise HTTPException(status_code=410, detail="Đề không còn source PDF để chỉnh sửa")
    job_id, job_dir = job_store.create(
        filename=f"{component}-{exam_id}.pdf",
        exam_type=component,
        file_hash=hashlib.sha256(
            f"edit:{exam_id}:{revision}:{editor_user_id}:{uuid.uuid4()}".encode()
        ).hexdigest(),
        owner_user_id=editor_user_id,
    )
    try:
        if storage is not None and settings.persistence_enabled:
            source_key = source_object_key or f"jobs/{source_job_id}/input.pdf"
            source_pdf_bucket = source_bucket or settings.minio_bucket_sources
            storage.client.stat_object(source_pdf_bucket, storage.safe_key(source_key))
            if source_pdf_bucket != settings.minio_bucket_sources:
                raise RuntimeError("Source PDF phải nằm trong bucket nguồn nội bộ")
            storage.copy_object(
                source_pdf_bucket,
                source_key,
                f"jobs/{job_id}/input.pdf",
            )
            storage.get_file(source_pdf_bucket, source_key, job_dir / "input.pdf")
            copied_by_folder: dict[str, int] = {"assets": 0, "pages": 0, "audio": 0}
            for folder, bucket in (
                ("assets", settings.minio_bucket_assets),
                ("pages", settings.minio_bucket_assets),
                ("audio", settings.minio_bucket_audio),
            ):
                if valid_source_job:
                    prefix = f"jobs/{source_job_id}/{folder}/"
                    for key in storage.list_prefix(bucket, prefix):
                        filename = Path(key).name
                        storage.copy_object(
                            bucket,
                            key,
                            f"jobs/{job_id}/{folder}/{filename}",
                        )
                        storage.get_file(bucket, key, job_dir / folder / filename)
                        copied_by_folder[folder] += 1

            # Job media expires; immutable version assets do not. Rehydrate
            # crops/audio from the pinned version when the original prefix has
            # already been purged.
            required_assets = {
                str(asset.get("id") or "")
                for stimulus in payload.get("stimuli", [])
                for asset in stimulus.get("assets", [])
            }
            required_audio = {
                str(audio.get("id") or "") for audio in payload.get("audios", [])
            }
            for asset in version_assets or []:
                filename = Path(str(asset.get("filename") or "")).name
                kind = str(asset.get("kind") or "")
                folder = "audio" if kind == "audio" else "assets"
                required = required_audio if folder == "audio" else required_assets
                if not filename or filename not in required:
                    continue
                destination = f"jobs/{job_id}/{folder}/{filename}"
                local_destination = job_dir / folder / filename
                if local_destination.is_file():
                    continue
                bucket = str(asset["bucket"])
                key = str(asset["object_key"])
                storage.copy_object(bucket, key, destination)
                storage.get_file(bucket, key, local_destination)
                copied_by_folder[folder] += 1

            if copied_by_folder["pages"] == 0:
                from pdf2image import convert_from_path, pdfinfo_from_path

                page_count = int(pdfinfo_from_path(str(job_dir / "input.pdf"))["Pages"])
                if page_count < 1 or page_count > settings.max_pdf_pages:
                    raise RuntimeError("Source PDF có số trang không hợp lệ")
                render_dir = job_dir / "rendered-pages"
                render_dir.mkdir(parents=True, exist_ok=True)
                rendered = convert_from_path(
                    str(job_dir / "input.pdf"),
                    dpi=140,
                    fmt="jpeg",
                    output_folder=str(render_dir),
                    paths_only=True,
                    thread_count=2,
                )
                for page_number, rendered_path in enumerate(rendered, start=1):
                    filename = f"page-{page_number:03d}.jpg"
                    destination = job_dir / "pages" / filename
                    shutil.move(str(rendered_path), destination)
                    storage.put_file(
                        settings.minio_bucket_assets,
                        f"jobs/{job_id}/pages/{filename}",
                        destination,
                        "image/jpeg",
                    )
                shutil.rmtree(render_dir, ignore_errors=True)
        else:
            source_dir = job_store.job_dir(source_job_id)
            source_pdf = source_dir / "input.pdf"
            if not source_pdf.is_file():
                raise FileNotFoundError("source PDF")
            shutil.copy2(source_pdf, job_dir / "input.pdf")
            for folder in ("assets", "pages", "audio"):
                for source in (source_dir / folder).glob("*"):
                    if source.is_file():
                        shutil.copy2(source, job_dir / folder / source.name)
    except Exception as exc:
        if hasattr(job_store, "evict_local"):
            job_store.evict_local(job_id)
        raise HTTPException(
            status_code=410,
            detail="Source PDF/media của đề không còn đầy đủ để mở edit session",
        ) from exc

    for stimulus in payload["stimuli"]:
        for asset in stimulus.get("assets", []):
            asset["url"] = f"/api/extractions/{job_id}/assets/{asset.get('id')}"
    for audio in payload["audios"]:
        audio["url"] = f"/api/extractions/{job_id}/audio/{audio.get('id')}"
    state = job_store.read(job_id)
    state.update(
        {
            "status": "review",
            "stage": "Bản nháp chỉnh sửa từ Kho đề thi chung",
            "progress": 100,
            "returned_count": len(payload["questions"]),
            "requested_count": len(payload["questions"]),
            **payload,
        }
    )
    state.setdefault("metadata", {}).update(
        {
            "source_exam_id": exam_id,
            "base_revision": revision,
            "page_count": len(list((job_dir / "pages").glob("page-*.jpg"))),
        }
    )
    job_store.write(job_id, state)
    return job_id


@router.post("/exam-bank/{exam_id}/edit-sessions", status_code=201)
def create_edit_session(exam_id: str, request: Request) -> dict[str, Any]:
    identity = _identity(request)
    _require_manager(identity)
    now = utcnow()
    with session_scope() as session:
        exam = _shared_exam(session, exam_id, identity)
        version = session.get(ExamVersion, exam.current_version_id) if exam.current_version_id else None
        if version is None:
            raise HTTPException(status_code=409, detail="Đề chưa có version khả dụng")
        active_sessions = session.scalars(
            select(ExamEditSession)
            .where(
                ExamEditSession.exam_id == exam.id,
                ExamEditSession.editor_user_id == identity["user_id"],
                ExamEditSession.status == "active",
            )
            .order_by(ExamEditSession.updated_at.desc())
        ).all()
        for active in active_sessions:
            expires_at = active.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=now.tzinfo)
            if expires_at > now and active.base_revision == exam.content_revision:
                return {
                    "id": active.id,
                    "exam_id": exam.id,
                    "base_revision": active.base_revision,
                    "job_ids": active.job_ids,
                    "expires_at": active.expires_at,
                    "resumed": True,
                }
            active.status = "expired"
        snapshot = copy.deepcopy(version.payload or {})
        base_revision = exam.content_revision
        exam_type = exam.exam_type
        original_job_id = str(exam.job_id or snapshot.get("job_id") or "")
        source_jobs = dict(snapshot.get("component_job_ids") or {})
        sources = {
            source.component: source
            for source in session.scalars(
                select(ExamSource).where(ExamSource.exam_id == exam.id)
            ).all()
        }
        immutable_assets = [
            {
                "kind": asset.kind,
                "bucket": asset.bucket,
                "object_key": asset.object_key,
                "filename": asset.filename,
            }
            for asset in session.scalars(
                select(ExamVersionAsset).where(
                    ExamVersionAsset.exam_version_id == version.id
                )
            ).all()
        ]

    components = ["listening", "reading"] if exam_type == "combined" else [exam_type]
    job_ids: dict[str, str] = {}
    for component in components:
        source_job_id = str(source_jobs.get(component) or original_job_id)
        durable_source = sources.get(component) or sources.get("main")
        job_ids[component] = _clone_job(
            source_job_id=source_job_id,
            component=component,
            editor_user_id=identity["user_id"],
            exam_id=exam_id,
            revision=base_revision,
            payload=_component_payload(snapshot, component),
            source_bucket=durable_source.bucket if durable_source else None,
            source_object_key=durable_source.object_key if durable_source else None,
            version_assets=immutable_assets,
        )

    with session_scope() as session:
        exam = _shared_exam(session, exam_id, identity)
        edit_session = ExamEditSession(
            exam_id=exam.id,
            editor_user_id=identity["user_id"],
            base_revision=base_revision,
            job_ids=job_ids,
            status="active",
            expires_at=utcnow() + timedelta(hours=2),
        )
        session.add(edit_session)
        session.flush()
        return {
            "id": edit_session.id,
            "exam_id": exam.id,
            "base_revision": edit_session.base_revision,
            "job_ids": edit_session.job_ids,
            "expires_at": edit_session.expires_at,
            "resumed": False,
        }


@router.delete("/exam-bank/edit-sessions/{session_id}")
def cancel_edit_session(session_id: str, request: Request) -> dict[str, bool]:
    identity = _identity(request)
    _require_manager(identity)
    with session_scope() as session:
        edit_session = session.get(ExamEditSession, session_id)
        if edit_session is None or edit_session.editor_user_id != identity["user_id"]:
            raise HTTPException(status_code=404, detail="Không tìm thấy edit session")
        edit_session.status = "cancelled"
        job_ids = list((edit_session.job_ids or {}).values())
        for job_id in job_ids:
            job = session.get(Job, job_id)
            if job is not None:
                session.delete(job)
    if storage is not None:
        for job_id in job_ids:
            for bucket, folder in (
                (settings.minio_bucket_sources, ""),
                (settings.minio_bucket_assets, "assets"),
                (settings.minio_bucket_assets, "pages"),
                (settings.minio_bucket_audio, "audio"),
            ):
                prefix = f"jobs/{job_id}/{folder}" if folder else f"jobs/{job_id}/"
                storage.remove_prefix(bucket, prefix)
    return {"ok": True}


@router.post("/exam-bank/edit-sessions/{session_id}/finalize")
def finalize_edit_session(
    session_id: str, body: EditSessionFinalize, request: Request
) -> dict[str, Any]:
    identity = _identity(request)
    _require_manager(identity)
    now = utcnow()
    with session_scope() as session:
        edit_session = session.get(ExamEditSession, session_id)
        if edit_session is None or edit_session.editor_user_id != identity["user_id"]:
            raise HTTPException(status_code=404, detail="Không tìm thấy edit session")
        if edit_session.status != "active":
            raise HTTPException(status_code=409, detail="Edit session không còn hoạt động")
        expires_at = edit_session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=now.tzinfo)
        if expires_at <= now:
            edit_session.status = "expired"
            raise HTTPException(status_code=410, detail="Edit session đã hết hạn")
        exam = _shared_exam(session, edit_session.exam_id, identity)
        base_revision = edit_session.base_revision
        job_ids = dict(edit_session.job_ids or {})
        title = " ".join((body.title or exam.title).split())
        category = exam.category
        if body.tag_id is not None:
            tag = session.get(ExamTag, body.tag_id)
            if tag is None:
                raise HTTPException(status_code=404, detail="Không tìm thấy Tag")
            category = tag.name
        elif body.tag is not None:
            tag_key = normalized_name_key(body.tag)
            if tag_key:
                tag = session.scalar(select(ExamTag).where(ExamTag.name_key == tag_key))
                if tag is None:
                    raise HTTPException(status_code=404, detail="Tag chưa tồn tại trong Kho chung")
                category = tag.name
            else:
                category = ""

    from job_store import store as job_store

    states: dict[str, dict[str, Any]] = {}
    try:
        states = {component: job_store.read(job_id) for component, job_id in job_ids.items()}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="Draft media đã hết hạn") from exc
    for state in states.values():
        if state.get("status") not in {"review", "ready"}:
            raise HTTPException(status_code=409, detail="Draft chưa sẵn sàng finalize")

    if set(states) == {"listening", "reading"}:
        questions = sorted(
            (states["listening"].get("questions") or [])
            + (states["reading"].get("questions") or []),
            key=lambda question: int(question["number"]),
        )
        stimuli = (states["listening"].get("stimuli") or []) + (
            states["reading"].get("stimuli") or []
        )
        solutions = (states["listening"].get("solutions") or []) + (
            states["reading"].get("solutions") or []
        )
        audios = states["listening"].get("audios") or []
        exam_type = "combined"
    else:
        exam_type, state = next(iter(states.items()))
        questions = state.get("questions") or []
        stimuli = state.get("stimuli") or []
        solutions = state.get("solutions") or []
        audios = state.get("audios") or []
    expected = (
        set(range(1, 201))
        if exam_type == "combined"
        else set(range(1, 101))
        if exam_type == "listening"
        else set(range(101, 201))
    )
    actual = {int(question.get("number", 0)) for question in questions}
    if actual != expected:
        raise HTTPException(status_code=422, detail="Draft không có đủ câu TOEIC yêu cầu")
    payload = {
        "schema_version": 2,
        "job_id": "+".join(job_ids.values()),
        "component_job_ids": job_ids if exam_type == "combined" else {},
        "exam_type": exam_type,
        "requested_count": len(questions),
        "returned_count": len(questions),
        "total": len(questions),
        "questions": questions,
        "stimuli": stimuli,
        "solutions": solutions,
        "audios": audios,
        "audio": next((audio for audio in audios if audio.get("part") == "full"), None),
        "title": title,
        "category": category,
        "exam_id": edit_session.exam_id,
    }
    from platform_api import persist_final_exam

    exam_id = persist_final_exam(
        payload,
        job_id=payload["job_id"],
        owner_user_id=identity["user_id"],
        title=title,
        category=category,
        target_exam_id=edit_session.exam_id,
        base_revision=base_revision,
    )
    with session_scope() as session:
        stored_session = session.get(ExamEditSession, session_id)
        exam = session.get(Exam, exam_id)
        if stored_session is not None:
            stored_session.status = "finalized"
            stored_session.updated_at = utcnow()
        return {
            "exam_id": exam_id,
            "revision": exam.content_revision if exam else base_revision + 1,
            "current_version_id": exam.current_version_id if exam else None,
        }


@router.get("/exam-bank")
def list_exam_bank(
    request: Request,
    search: str = Query(default="", max_length=120),
    tag_id: str | None = Query(default=None),
    kind: Literal["listening", "reading", "combined"] | None = Query(default=None),
    include_archived: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    identity = _identity(request)
    with session_scope() as session:
        filters: list[Any] = [
            Exam.library_scope == "teacher_shared",
            Exam.deleted_at.is_(None),
            *exam_bank_visibility_filters(identity),
        ]
        if not include_archived or identity["role"] == "student":
            filters.append(Exam.archived_at.is_(None))
        if search.strip():
            filters.append(Exam.title.ilike(f"%{search.strip()}%"))
        if kind:
            filters.append(Exam.exam_type == kind)
        selected_tag: ExamTag | None = None
        if tag_id:
            selected_tag = session.get(ExamTag, tag_id)
            if selected_tag is None:
                raise HTTPException(status_code=404, detail="Không tìm thấy Tag")
            filters.append(Exam.category == selected_tag.name)

        total = int(session.scalar(select(func.count(Exam.id)).where(*filters)) or 0)
        exams = session.scalars(
            select(Exam)
            .where(*filters)
            .order_by(Exam.updated_at.desc(), Exam.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        exam_ids = [exam.id for exam in exams]
        contributor_ids = {exam.owner_user_id for exam in exams if exam.owner_user_id}
        contributors = {
            user.id: user
            for user in session.scalars(select(User).where(User.id.in_(contributor_ids))).all()
        } if contributor_ids else {}
        attempt_stats: dict[str, tuple[int, Any]] = {}
        if exam_ids:
            attempt_stats = {
                exam_id: (count, last_submitted)
                for exam_id, count, last_submitted in session.execute(
                    select(
                        Attempt.exam_id,
                        func.count(Attempt.id),
                        func.max(Attempt.submitted_at),
                    )
                    .where(
                        Attempt.user_id == identity["user_id"],
                        Attempt.exam_id.in_(exam_ids),
                    )
                    .group_by(Attempt.exam_id)
                )
            }
        tags_by_key = {
            tag.name_key: tag
            for tag in session.scalars(select(ExamTag)).all()
        }
        items = []
        for exam in exams:
            contributor = contributors.get(exam.owner_user_id)
            tag = tags_by_key.get(normalized_name_key(exam.category))
            attempt_count, last_attempt_at = attempt_stats.get(exam.id, (0, None))
            denominator = max(1, exam.question_count)
            items.append(
                {
                    "id": exam.id,
                    "slug": exam.slug,
                    "client_exam_id": (
                        exam.client_exam_id
                        if exam.owner_user_id == identity["user_id"]
                        else None
                    ),
                    "title": exam.title,
                    "exam_type": exam.exam_type,
                    "category": exam.category,
                    "tag": {"id": tag.id, "name": tag.name} if tag else None,
                    "contributor": (
                        {"id": contributor.id, "display_name": contributor.display_name}
                        if contributor else None
                    ),
                    "revision": exam.content_revision,
                    "current_version_id": exam.current_version_id,
                    "question_count": exam.question_count,
                    "answer_key_count": exam.answer_key_count,
                    "solution_entry_count": exam.solution_entry_count,
                    "solution_question_count": exam.solution_question_count,
                    "solution_coverage_percent": round(
                        exam.solution_question_count * 100 / denominator
                    ),
                    "duration_minutes": exam.duration_minutes,
                    "status": "archived" if exam.archived_at else exam.status,
                    "attempt_count": int(attempt_count),
                    "last_attempt_at": last_attempt_at,
                    "updated_at": exam.updated_at,
                    "created_at": exam.created_at,
                }
            )
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }


@router.get("/exam-bank/tags")
def list_exam_bank_tags(request: Request) -> dict[str, Any]:
    identity = _identity(request)
    with session_scope() as session:
        visible_categories = list(
            session.scalars(
                select(Exam.category).where(
                    Exam.library_scope == "teacher_shared",
                    Exam.deleted_at.is_(None),
                    Exam.archived_at.is_(None),
                    *exam_bank_visibility_filters(identity),
                )
            )
        )
        tags = (
            session.scalars(
                select(ExamTag)
                .where(ExamTag.name.in_(visible_categories))
                .order_by(ExamTag.name_key)
            ).all()
            if visible_categories
            else []
        )
        return {"items": [{"id": tag.id, "name": tag.name} for tag in tags]}


@router.post("/exam-bank/tags", status_code=201)
def create_exam_bank_tag(body: ExamTagCreate, request: Request) -> dict[str, str]:
    identity = _identity(request)
    _require_manager(identity)
    name = " ".join(body.name.split())
    name_key = normalized_name_key(name)
    if not name_key:
        raise HTTPException(status_code=422, detail="Tên Tag không được để trống")
    try:
        with session_scope() as session:
            tag = ExamTag(name=name, name_key=name_key)
            session.add(tag)
            session.flush()
            return {"id": tag.id, "name": tag.name}
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Tag đã tồn tại") from exc


@router.patch("/exam-bank/tags/{tag_id}")
def rename_exam_bank_tag(
    tag_id: str, body: ExamTagUpdate, request: Request
) -> dict[str, Any]:
    identity = _identity(request)
    _require_manager(identity)
    name = " ".join(body.name.split())
    name_key = normalized_name_key(name)
    if not name_key:
        raise HTTPException(status_code=422, detail="Tên Tag không được để trống")
    try:
        with session_scope() as session:
            tag = session.scalar(
                select(ExamTag).where(ExamTag.id == tag_id).with_for_update()
            )
            if tag is None:
                raise HTTPException(status_code=404, detail="Không tìm thấy Tag")
            duplicate = session.scalar(
                select(ExamTag.id).where(
                    ExamTag.name_key == name_key,
                    ExamTag.id != tag.id,
                )
            )
            if duplicate:
                raise HTTPException(status_code=409, detail="Tag đã tồn tại")
            old_name = tag.name
            if old_name == name:
                return {"id": tag.id, "name": tag.name, "updated_exams": 0}

            if identity["role"] != "admin":
                foreign_use = session.scalar(
                    select(Exam.id).where(
                        Exam.library_scope == "teacher_shared",
                        Exam.category == old_name,
                        Exam.owner_user_id != identity["user_id"],
                    )
                )
                if foreign_use:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Tag đang được dùng bởi kho đề của Teacher khác; "
                            "không thể đổi tên dùng chung."
                        ),
                    )

            affected_filters: list[Any] = [
                Exam.library_scope == "teacher_shared",
                Exam.category == old_name,
            ]
            if identity["role"] != "admin":
                affected_filters.append(Exam.owner_user_id == identity["user_id"])
            affected = list(
                session.scalars(
                    select(Exam)
                    .where(*affected_filters)
                    .with_for_update()
                )
            )
            tag.name = name
            tag.name_key = name_key
            for exam in affected:
                exam.category = name
                exam.payload = {**(exam.payload or {}), "category": name}
                exam.updated_at = utcnow()
            session.flush()
            return {"id": tag.id, "name": tag.name, "updated_exams": len(affected)}
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Tag đã tồn tại") from exc


@router.delete("/exam-bank/tags/{tag_id}")
def delete_exam_bank_tag(tag_id: str, request: Request) -> dict[str, Any]:
    identity = _identity(request)
    _require_manager(identity)
    with session_scope() as session:
        tag = session.scalar(
            select(ExamTag).where(ExamTag.id == tag_id).with_for_update()
        )
        if tag is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy Tag")
        if identity["role"] != "admin":
            foreign_use = session.scalar(
                select(Exam.id).where(
                    Exam.library_scope == "teacher_shared",
                    Exam.category == tag.name,
                    Exam.owner_user_id != identity["user_id"],
                )
            )
            if foreign_use:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Tag đang được dùng bởi kho đề của Teacher khác; "
                        "không thể xoá dùng chung."
                    ),
                )
        affected_filters: list[Any] = [
            Exam.library_scope == "teacher_shared",
            Exam.category == tag.name,
        ]
        if identity["role"] != "admin":
            affected_filters.append(Exam.owner_user_id == identity["user_id"])
        affected = list(
            session.scalars(
                select(Exam)
                .where(*affected_filters)
                .with_for_update()
            )
        )
        for exam in affected:
            exam.category = ""
            exam.payload = {**(exam.payload or {}), "category": ""}
            exam.updated_at = utcnow()
        deleted_name = tag.name
        session.delete(tag)
        session.flush()
        return {
            "deleted": True,
            "id": tag_id,
            "name": deleted_name,
            "updated_exams": len(affected),
        }


@router.patch("/exam-bank/{exam_id}")
def patch_exam_bank(
    exam_id: str, body: ExamBankPatch, request: Request
) -> dict[str, Any]:
    identity = _identity(request)
    _require_manager(identity)
    with session_scope() as session:
        exam = _shared_exam(session, exam_id, identity, lock=True)
        if exam.content_revision != body.base_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "exam_revision_conflict",
                    "current_revision": exam.content_revision,
                },
            )
        changed: dict[str, Any] = {}
        if body.title is not None:
            title = " ".join(body.title.split())
            title_key = teacher_scoped_title_key(str(exam.owner_user_id or ""), title)
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
                raise HTTPException(status_code=409, detail="Tên đề đã tồn tại trong Kho chung")
            changed["title"] = {"from": exam.title, "to": title}
            exam.title = title
            exam.shared_title_key = title_key
            exam.payload = {**(exam.payload or {}), "title": title}
        if body.tag_id is not None:
            tag = session.get(ExamTag, body.tag_id)
            if tag is None:
                raise HTTPException(status_code=404, detail="Không tìm thấy Tag")
            changed["tag"] = {"from": exam.category, "to": tag.name}
            exam.category = tag.name
            exam.payload = {**(exam.payload or {}), "category": tag.name}
        if body.archived is not None:
            changed["archived"] = body.archived
            exam.archived_at = utcnow() if body.archived else None
        if not changed:
            return {"id": exam.id, "revision": exam.content_revision}
        previous_revision = exam.content_revision
        exam.content_revision += 1
        exam.last_edited_by_user_id = identity["user_id"]
        exam.updated_at = utcnow()
        if body.title is not None or body.tag_id is not None:
            from classroom_api import _snapshot_exam

            version = _snapshot_exam(session, exam, identity["user_id"])
            exam.current_version_id = version.id
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="exam_bank.updated",
                target_type="exam",
                target_id=exam.id,
                detail={
                    "previous_revision": previous_revision,
                    "new_revision": exam.content_revision,
                    "changes": changed,
                },
            )
        )
        try:
            session.flush()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Tên đề đã tồn tại trong Kho chung") from exc
        return {"id": exam.id, "revision": exam.content_revision, "changes": changed}


@router.delete("/exam-bank/{exam_id}")
def delete_exam_bank(exam_id: str, request: Request) -> dict[str, bool]:
    identity = _identity(request)
    _require_manager(identity)
    with session_scope() as session:
        exam = _shared_exam(session, exam_id, identity, lock=True)
        exam.deleted_at = utcnow()
        exam.archived_at = exam.archived_at or utcnow()
        exam.shared_title_key = None
        exam.content_revision += 1
        exam.last_edited_by_user_id = identity["user_id"]
        session.execute(
            update(PublicExamShare)
            .where(PublicExamShare.exam_id == exam.id)
            .values(is_active=False, updated_at=utcnow())
        )
        session.add(
            AuditLog(
                actor_user_id=identity["user_id"],
                action="exam_bank.deleted",
                target_type="exam",
                target_id=exam.id,
                detail={"soft_delete": True, "revision": exam.content_revision},
            )
        )
    return {"ok": True}


@router.post("/exam-bank/{exam_id}/attempts")
def start_exam_bank_attempt(
    exam_id: str, body: ExamBankAttemptCreate, request: Request
) -> dict[str, Any]:
    identity = _identity(request)
    with session_scope() as session:
        # The immutable current version is safe to read concurrently. Locking
        # the parent Exam row for every student serialized the 300-user start
        # burst even when no version had to be created.
        exam = _shared_exam(session, exam_id, identity)
        if exam.archived_at is not None:
            raise HTTPException(status_code=409, detail="Đề đã được lưu trữ")
        version = session.get(ExamVersion, exam.current_version_id) if exam.current_version_id else None
        if version is None:
            if identity["role"] not in {"teacher", "admin"}:
                raise HTTPException(status_code=409, detail="Đề chưa có version khả dụng")
            from classroom_api import _snapshot_exam

            # Only the slow first-publication path needs a parent-row lock.
            exam = _shared_exam(session, exam_id, identity, lock=True)
            version = (
                session.get(ExamVersion, exam.current_version_id)
                if exam.current_version_id
                else None
            )
            if version is None:
                version = _snapshot_exam(session, exam, identity["user_id"])
                exam.current_version_id = version.id

        existing = session.scalar(
            select(Attempt)
            .where(
                Attempt.user_id == identity["user_id"],
                Attempt.exam_id == exam.id,
                Attempt.class_assignment_id.is_(None),
                Attempt.status == "in_progress",
            )
            .order_by(Attempt.started_at.desc())
            .with_for_update()
        )
        if existing is None:
            from classroom_api import _available_part_numbers

            available_parts = set(_available_part_numbers(version.payload or {}))
            selected_parts = set(body.part_numbers or available_parts)
            if not selected_parts or not selected_parts.issubset(available_parts):
                raise HTTPException(status_code=422, detail="Part đã chọn không có trong đề")
            duration = body.duration_seconds or version.duration_minutes * 60
            existing = Attempt(
                exam_id=exam.id,
                exam_version_id=version.id,
                user_id=identity["user_id"],
                launch_mode=body.launch_mode,
                selected_part_numbers=sorted(selected_parts),
                duration_seconds=duration,
                time_left_seconds=duration,
                deadline_at=utcnow() + timedelta(seconds=duration),
            )
            try:
                # The partial unique index makes duplicate start requests
                # converge on one personal attempt. A savepoint lets the
                # losing request re-read that committed row without aborting
                # the surrounding transaction.
                with session.begin_nested():
                    session.add(existing)
                    session.flush()
            except IntegrityError:
                existing = session.scalar(
                    select(Attempt)
                    .where(
                        Attempt.user_id == identity["user_id"],
                        Attempt.exam_id == exam.id,
                        Attempt.class_assignment_id.is_(None),
                        Attempt.status == "in_progress",
                    )
                    .order_by(Attempt.started_at.desc())
                    .with_for_update()
                )
                if existing is None:
                    raise
        else:
            version = session.get(ExamVersion, existing.exam_version_id)
            if version is None:
                raise HTTPException(status_code=409, detail="Version của lượt làm không còn tồn tại")

        answers = {
            answer.question_number: answer.selected
            for answer in session.scalars(
                select(AttemptAnswer).where(AttemptAnswer.attempt_id == existing.id)
            )
        }

        from classroom_api import _exam_for_student

        exam_payload = copy.deepcopy(
            _exam_for_student(session, version, reveal_answers=False, attempt=existing)
        )
        return {
            "attempt_id": existing.id,
            "resumed": bool(answers or existing.answer_revision),
            "exam": exam_payload,
            "answers": answers,
            "launch_mode": existing.launch_mode,
            "selected_part_numbers": existing.selected_part_numbers,
            "duration_seconds": existing.duration_seconds,
            "time_left_seconds": existing.time_left_seconds,
            "current_question_number": existing.current_question_number,
            "deadline_at": existing.deadline_at,
            "exam_version_id": version.id,
            "exam_content_hash": version.content_hash,
            "accepted_revision": existing.answer_revision,
        }


@v2_router.post("/exam-bank/{exam_id}/attempt-bootstrap")
def start_exam_bank_attempt_v2(
    exam_id: str, body: ExamBankAttemptCreate, request: Request
) -> dict[str, Any]:
    """Stable bootstrap envelope for new clients.

    The v1 response remains untouched for existing web/desktop clients. New
    clients can migrate to a named manifest/attempt envelope without adding a
    second database read or changing answer persistence semantics.
    """
    payload = start_exam_bank_attempt(exam_id, body, request)
    return {
        "schema_version": 2,
        "attempt": {
            "id": payload["attempt_id"],
            "launch_mode": payload["launch_mode"],
            "selected_part_numbers": payload["selected_part_numbers"],
            "duration_seconds": payload["duration_seconds"],
            "time_left_seconds": payload["time_left_seconds"],
            "current_question_number": payload["current_question_number"],
            "deadline_at": payload["deadline_at"],
            "exam_version_id": payload["exam_version_id"],
            "content_hash": payload["exam_content_hash"],
            "accepted_revision": payload["accepted_revision"],
            "resumed": payload["resumed"],
        },
        "manifest": payload["exam"],
        "answers": payload["answers"],
    }


@router.get("/attempts/{attempt_id}/solutions")
def attempt_solutions(attempt_id: str, request: Request) -> dict[str, Any]:
    # Personal `user` exams and classroom `student` exams share the same
    # immutable-attempt ownership rule. The general exam-bank identity helper
    # intentionally excludes `user`, so use the narrower owner check here.
    identity = current_identity(request)
    with session_scope() as session:
        attempt = session.get(Attempt, attempt_id)
        if (
            attempt is None
            or attempt.user_id != identity["user_id"]
            or attempt.status != "submitted"
            or not attempt.exam_version_id
        ):
            raise HTTPException(status_code=404, detail="Không tìm thấy lời giải của lượt làm")
        version = session.get(ExamVersion, attempt.exam_version_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Version đề không còn tồn tại")
        from classroom_api import _exam_for_student

        payload = copy.deepcopy(
            _exam_for_student(session, version, reveal_answers=True, attempt=attempt)
        )
        answers = {
            str(answer.question_number): answer.selected
            for answer in session.scalars(
                select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id)
            )
        }
        return {
            "attempt_id": attempt.id,
            "exam_id": attempt.exam_id,
            "exam_version_id": version.id,
            "content_hash": version.content_hash,
            "title": version.title,
            "exam_type": version.exam_type,
            "questions": payload.get("questions") or [],
            "stimuli": payload.get("stimuli") or [],
            "audio": payload.get("audio"),
            "audios": payload.get("audios") or [],
            "solutions": payload.get("solutions") or [],
            "student_answers": answers,
        }


@router.get("/system/data-epoch")
def get_data_epoch(request: Request) -> dict[str, str]:
    # Legacy `user` Desktop clients still need the epoch in order to quarantine
    # stale local data, even though they do not have access to the shared bank.
    current_identity(request)
    with session_scope() as session:
        state = session.get(SystemState, "data_epoch")
        if state is None:
            # The migration creates this row in production. Initializing it
            # here also makes fresh metadata/create_all deployments safe.
            state = SystemState(key="data_epoch", value=str(uuid.uuid4()))
            session.add(state)
            session.flush()
        return {"data_epoch": state.value}


@router.post("/solution-imports", status_code=202)
async def create_solution_import(
    request: Request,
    file: UploadFile = File(...),
    exam_type: Literal["listening", "reading"] = Form(...),
) -> dict[str, Any]:
    identity = current_identity(request)
    if identity["role"] not in {"user", "teacher", "admin"}:
        raise HTTPException(
            status_code=403,
            detail="Chỉ người tạo đề cá nhân, Teacher hoặc Admin được import lời giải",
        )
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.casefold()
    if suffix not in {".docx", ".doc", ".pdf"}:
        raise HTTPException(status_code=422, detail="Chỉ chấp nhận .docx, .doc hoặc .pdf")
    if storage is None:
        raise HTTPException(status_code=503, detail="MinIO chưa sẵn sàng")
    now = utcnow()
    with session_scope() as session:
        # Serialize quota checks for the same Teacher. This closes the race in
        # which two multipart requests both observed zero active imports.
        session.scalar(
            select(User.id)
            .where(User.id == identity["user_id"])
            .with_for_update()
        )
        active = int(
            session.scalar(
                select(func.count(SolutionImport.id)).where(
                    SolutionImport.owner_user_id == identity["user_id"],
                    SolutionImport.status.in_(["queued", "processing"]),
                )
            )
            or 0
        )
        if active:
            raise HTTPException(
                status_code=409,
                detail="Mỗi tài khoản chỉ được chạy một solution import cùng lúc",
            )
        recent = int(
            session.scalar(
                select(func.count(SolutionImport.id)).where(
                    SolutionImport.owner_user_id == identity["user_id"],
                    SolutionImport.created_at >= now - timedelta(minutes=10),
                )
            )
            or 0
        )
        if recent >= 5:
            raise HTTPException(status_code=429, detail="Tối đa 5 import trong 10 phút")
        import_row = SolutionImport(
            owner_user_id=identity["user_id"],
            exam_type=exam_type,
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            status="queued",
            expires_at=now + timedelta(hours=24),
        )
        session.add(import_row)
        session.flush()
        import_id = import_row.id

    chunks: list[bytes] = []
    size = 0
    try:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 20 * 1024 * 1024:
                with session_scope() as session:
                    row = session.get(SolutionImport, import_id)
                    if row is not None:
                        row.status = "failed"
                        row.error = "File lời giải vượt quá 20 MiB"
                raise HTTPException(status_code=413, detail="File lời giải vượt quá 20 MiB")
            chunks.append(chunk)
    finally:
        await file.close()
    if size == 0:
        with session_scope() as session:
            row = session.get(SolutionImport, import_id)
            if row is not None:
                row.status = "failed"
                row.error = "File lời giải rỗng"
        raise HTTPException(status_code=422, detail="File lời giải rỗng")
    object_key = f"solution-imports/{import_id}/source{suffix}"
    try:
        storage.put_bytes(
            settings.minio_bucket_sources,
            object_key,
            b"".join(chunks),
            file.content_type or "application/octet-stream",
        )
    except Exception as exc:
        with session_scope() as session:
            row = session.get(SolutionImport, import_id)
            if row is not None:
                row.status = "failed"
                row.error = "Không lưu được file tạm trên MinIO"
        raise HTTPException(status_code=502, detail="Không lưu được file import") from exc
    with session_scope() as session:
        row = session.get(SolutionImport, import_id)
        if row is not None:
            row.bucket = settings.minio_bucket_sources
            row.object_key = object_key
            row.size = size

    from solution_tasks import process_solution_import

    if settings.use_celery:
        process_solution_import.apply_async(args=[import_id], queue="ocr")
    else:
        try:
            process_solution_import.run(import_id)
        except Exception:
            # The task already persisted a safe failed state. Returning the ID
            # lets the editor render that error through the normal poll path.
            pass
    with session_scope() as session:
        row = session.get(SolutionImport, import_id)
        status = row.status if row is not None else "queued"
    return {"id": import_id, "status": status, "expires_at": now + timedelta(hours=24)}


@router.get("/solution-imports/{import_id}")
def get_solution_import(import_id: str, request: Request) -> dict[str, Any]:
    identity = current_identity(request)
    if identity["role"] not in {"user", "teacher", "admin"}:
        raise HTTPException(status_code=403, detail="Không có quyền xem solution import")
    with session_scope() as session:
        row = session.get(SolutionImport, import_id)
        if row is None or row.owner_user_id != identity["user_id"]:
            raise HTTPException(status_code=404, detail="Không tìm thấy solution import")
        return {
            "id": row.id,
            "status": row.status,
            "exam_type": row.exam_type,
            "filename": row.filename,
            "size": row.size,
            "result": row.result or {},
            "issues": row.issues or [],
            "error": row.error,
            "expires_at": row.expires_at,
            "created_at": row.created_at,
        }
