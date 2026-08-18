"""Browser-owned OCR ingest contract.

No endpoint in this router accepts page images for OCR or stores per-page OCR
progress. The browser performs recognition and review, uploads only finalized
source/media assets to immutable MinIO keys, then commits a bounded manifest.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import pdfplumber
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from auth_service import require_roles
from config import settings
from database import session_scope
from exam_solutions import SolutionValidationError, validate_solutions
from models import Exam, ExamSource, Job, utcnow
from object_storage import storage
from platform_api import persist_final_exam
from schemas import Issue, Question, SolutionEntry, Stimulus, question_requires_printed_options, question_requires_printed_text


router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)

MAX_MANIFEST_BYTES = 5 * 1024 * 1024
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
MAX_QUESTIONS = 200
MAX_STIMULI = 200
MAX_ASSETS = 300
MAX_UPLOADS = 401
UPLOAD_POLICY_MINUTES = 15
SESSION_TTL_HOURS = 24
_PDF_VALIDATION_SLOTS = threading.BoundedSemaphore(3)
_SAFE_UPLOAD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ClientUploadDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    kind: Literal["source", "asset", "audio"]
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=160)
    size: int = Field(gt=0, le=MAX_SOURCE_BYTES)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not _SAFE_UPLOAD_ID.fullmatch(value):
            raise ValueError("upload id chỉ được chứa chữ, số, dấu chấm, gạch dưới/gạch ngang")
        return value

    @field_validator("sha256")
    @classmethod
    def valid_hash(cls, value: str | None) -> str | None:
        normalized = value.lower() if value else value
        if normalized and not _SHA256.fullmatch(normalized):
            raise ValueError("sha256 không hợp lệ")
        return normalized

    @model_validator(mode="after")
    def valid_mime(self) -> "ClientUploadDeclaration":
        expected = {
            "source": {"application/pdf"},
            "asset": {"image/webp"},
            "audio": {
                "audio/mpeg",
                "audio/mp4",
                "audio/wav",
                "audio/ogg",
                "audio/webm",
                "audio/flac",
                "audio/aac",
            },
        }[self.kind]
        if self.content_type.lower() not in expected:
            raise ValueError(f"MIME không hợp lệ cho {self.kind}")
        if self.kind == "asset" and self.size > 20 * 1024 * 1024:
            raise ValueError("asset đơn lẻ vượt giới hạn 20 MiB")
        return self


class ClientExtractionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=16, max_length=64)
    component: Literal["listening", "reading"]
    requested_count: int | None = Field(default=None, ge=1, le=100)
    source_sha256: str = Field(min_length=64, max_length=64)
    pipeline_version: Literal["client-tesseract-v1"] = "client-tesseract-v1"
    uploads: list[ClientUploadDeclaration] = Field(min_length=1, max_length=MAX_UPLOADS)

    @field_validator("client_request_id")
    @classmethod
    def valid_request_id(cls, value: str) -> str:
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError("client_request_id phải là UUID") from exc

    @field_validator("source_sha256")
    @classmethod
    def valid_source_hash(cls, value: str) -> str:
        normalized = value.lower()
        if not _SHA256.fullmatch(normalized):
            raise ValueError("source_sha256 không hợp lệ")
        return normalized

    @model_validator(mode="after")
    def valid_uploads(self) -> "ClientExtractionCreate":
        ids = [item.id for item in self.uploads]
        if len(ids) != len(set(ids)):
            raise ValueError("upload id bị trùng")
        sources = [item for item in self.uploads if item.kind == "source"]
        if len(sources) != 1:
            raise ValueError("mỗi session phải có đúng một source PDF")
        if sources[0].sha256 and sources[0].sha256 != self.source_sha256:
            raise ValueError("sha256 của source không khớp session")
        return self


class ClientManifestAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    page: int = Field(ge=1, le=MAX_PAGES)
    bbox: tuple[float, float, float, float]
    width: int = Field(gt=0, le=20_000)
    height: int = Field(gt=0, le=20_000)
    content_type: Literal["image/webp"] = "image/webp"
    size: int = Field(gt=0, le=20 * 1024 * 1024)
    upload_id: str = Field(min_length=1, max_length=80)

    @field_validator("bbox")
    @classmethod
    def valid_bbox(cls, value: tuple[float, float, float, float]):
        x0, y0, x1, y1 = value
        if not all(0 <= item <= 1 for item in value) or x0 >= x1 or y0 >= y1:
            raise ValueError("bbox phải được chuẩn hóa [0,1] và có diện tích dương")
        return value


class ClientManifestMedia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    upload_id: str = Field(min_length=1, max_length=80)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=160)
    size: int = Field(gt=0, le=50 * 1024 * 1024)
    part: Literal[
        "full",
        "directions_part_1",
        "part_1",
        "part_2",
        "part_3",
        "part_4",
    ] = "full"
    scope: Literal["full", "part", "question", "group"] = "part"
    question_numbers: list[int] = Field(default_factory=list, max_length=100)
    group_id: str | None = Field(default=None, max_length=80)


class ClientExtractionManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    pipeline_version: Literal["client-tesseract-v1"]
    source_sha256: str = Field(min_length=64, max_length=64)
    source_filename: str = Field(min_length=1, max_length=512)
    source_size: int = Field(gt=0, le=MAX_SOURCE_BYTES)
    page_count: int = Field(ge=1, le=MAX_PAGES)
    exam_type: Literal["listening", "reading"]
    requested_count: int | None = Field(default=None, ge=1, le=100)
    questions: list[Question] = Field(min_length=1, max_length=MAX_QUESTIONS)
    stimuli: list[Stimulus] = Field(default_factory=list, max_length=MAX_STIMULI)
    assets: list[ClientManifestAsset] = Field(default_factory=list, max_length=MAX_ASSETS)
    media: list[ClientManifestMedia] = Field(default_factory=list, max_length=100)
    solutions: list[SolutionEntry] = Field(default_factory=list, max_length=200)
    issues: list[Issue] = Field(default_factory=list, max_length=1000)
    answer_key: dict[str, str] = Field(default_factory=dict, max_length=MAX_QUESTIONS)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_sha256")
    @classmethod
    def valid_source_hash(cls, value: str) -> str:
        normalized = value.lower()
        if not _SHA256.fullmatch(normalized):
            raise ValueError("source_sha256 không hợp lệ")
        return normalized


class ClientExtractionCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=16, max_length=80)
    manifest_sha256: str = Field(min_length=64, max_length=64)
    manifest: ClientExtractionManifestV1
    title: str = Field(min_length=1, max_length=255)
    category: str = Field(default="", max_length=120)
    base_revision: int | None = Field(default=None, ge=1)
    target_exam_id: str | None = Field(default=None, max_length=36)
    is_full_test_component: bool = False


class RefreshUploadsRequest(BaseModel):
    upload_ids: list[str] = Field(min_length=1, max_length=MAX_UPLOADS)


class SolutionRowsRequest(BaseModel):
    exam_type: Literal["listening", "reading"]
    rows: list[SolutionEntry] = Field(max_length=200)


def _canonical_hash(manifest: ClientExtractionManifestV1) -> str:
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise HTTPException(status_code=413, detail="Manifest vượt giới hạn 5 MiB")
    return hashlib.sha256(payload).hexdigest()


def _upload_bucket(kind: str) -> str:
    if kind == "source":
        return settings.minio_bucket_sources
    if kind == "audio":
        return settings.minio_bucket_audio
    return settings.minio_bucket_assets


def _extension(upload: ClientUploadDeclaration) -> str:
    if upload.kind == "source":
        return ".pdf"
    if upload.kind == "asset":
        return ".webp"
    suffix = Path(upload.filename).suffix.lower()
    return suffix if suffix in {".mp3", ".m4a", ".wav", ".ogg", ".webm", ".flac", ".aac"} else ".bin"


def _upload_record(
    upload: ClientUploadDeclaration,
    *,
    reserved_exam_id: str,
    session_id: str,
) -> dict[str, Any]:
    key = (
        f"exams/{reserved_exam_id}/revisions/{session_id}/"
        f"{upload.kind}/{upload.id}{_extension(upload)}"
    )
    return {
        **upload.model_dump(mode="json"),
        "bucket": _upload_bucket(upload.kind),
        "object_key": key,
    }


def _policy(record: dict[str, Any]) -> dict[str, Any]:
    if storage is None:
        raise HTTPException(status_code=503, detail="Object storage chưa sẵn sàng")
    policy = storage.presigned_browser_post(
        record["bucket"],
        record["object_key"],
        content_type=record["content_type"],
        minimum_size=record["size"],
        maximum_size=record["size"],
        expires=timedelta(minutes=UPLOAD_POLICY_MINUTES),
    )
    return {"upload_id": record["id"], "kind": record["kind"], **policy}


def _job_for_owner(session_id: str, owner_user_id: str, *, lock: bool = False) -> Job:
    with session_scope() as session:
        query = select(Job).where(
            Job.id == session_id,
            Job.owner_user_id == owner_user_id,
            Job.ingest_mode == "client_ocr",
        )
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy client extraction session")
        session.expunge(row)
        return row


def _validate_manifest_structure(manifest: ClientExtractionManifestV1) -> None:
    start, end = (1, 100) if manifest.exam_type == "listening" else (101, 200)
    numbers = [item.number for item in manifest.questions]
    if len(numbers) != len(set(numbers)):
        raise HTTPException(status_code=422, detail="Manifest có số câu trùng")
    if any(number < start or number > end for number in numbers):
        raise HTTPException(status_code=422, detail="Số câu nằm ngoài component")
    expected_count = manifest.requested_count or 100
    if len(numbers) != expected_count:
        raise HTTPException(
            status_code=422,
            detail=f"Manifest có {len(numbers)}/{expected_count} câu; cần review trước khi lưu",
        )
    stimulus_ids = {item.id for item in manifest.stimuli}
    if len(stimulus_ids) != len(manifest.stimuli):
        raise HTTPException(status_code=422, detail="Stimulus id bị trùng")
    asset_by_id = {item.id: item for item in manifest.assets}
    if len(asset_by_id) != len(manifest.assets):
        raise HTTPException(status_code=422, detail="Asset id bị trùng")
    for question in manifest.questions:
        expected_letters = (
            ["A", "B", "C"]
            if manifest.exam_type == "listening" and 7 <= question.number <= 31
            else ["A", "B", "C", "D"]
        )
        letters = question.option_letters or expected_letters
        if any(letter not in {"A", "B", "C", "D"} for letter in letters):
            raise HTTPException(status_code=422, detail=f"Câu {question.number} có option letter không hợp lệ")
        if question_requires_printed_text(manifest.exam_type, question.number) and not question.text.strip():
            raise HTTPException(status_code=422, detail=f"Câu {question.number} thiếu nội dung")
        if question_requires_printed_options(manifest.exam_type, question.number):
            if len(letters) != len(set(letters)) or set(letters) != set(expected_letters):
                raise HTTPException(status_code=422, detail=f"Câu {question.number} thiếu option letter chuẩn")
            if any(not str(question.options.get(letter, "")).strip() for letter in expected_letters):
                raise HTTPException(status_code=422, detail=f"Câu {question.number} thiếu phương án")
        if question.stimulus_id and question.stimulus_id not in stimulus_ids:
            raise HTTPException(status_code=422, detail=f"Câu {question.number} tham chiếu stimulus không tồn tại")
        if any(issue in {"question_missing", "options_missing", "manual_review"} for issue in question.issues):
            raise HTTPException(status_code=422, detail=f"Câu {question.number} còn issue chưa review")
    for stimulus in manifest.stimuli:
        if any(number not in numbers for number in stimulus.question_numbers):
            raise HTTPException(status_code=422, detail=f"Stimulus {stimulus.id} tham chiếu câu không tồn tại")
        if "crop_review" in stimulus.issues:
            raise HTTPException(status_code=422, detail=f"Stimulus {stimulus.id} chưa review crop")
        for asset in stimulus.assets:
            if asset.id not in asset_by_id:
                raise HTTPException(status_code=422, detail=f"Stimulus {stimulus.id} tham chiếu asset chưa khai báo")
    referenced_asset_ids = {
        asset.id for stimulus in manifest.stimuli for asset in stimulus.assets
    }
    if referenced_asset_ids != set(asset_by_id):
        raise HTTPException(status_code=422, detail="Manifest có asset không được stimulus tham chiếu")
    if any(issue.severity == "error" for issue in manifest.issues):
        raise HTTPException(status_code=422, detail="Manifest còn issue OCR mức error")
    try:
        validate_solutions(
            [item.model_dump(mode="json") for item in manifest.solutions],
            manifest.exam_type,
        )
    except SolutionValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.issues) from exc
    for raw_number, letter in manifest.answer_key.items():
        try:
            number = int(raw_number)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Answer key có số câu không hợp lệ") from exc
        if number not in numbers or letter.upper() not in {"A", "B", "C", "D"}:
            raise HTTPException(status_code=422, detail=f"Answer key câu {raw_number} không hợp lệ")
    media_ids = {item.id for item in manifest.media}
    if len(media_ids) != len(manifest.media):
        raise HTTPException(status_code=422, detail="Media id bị trùng")
    for media in manifest.media:
        if any(number not in numbers for number in media.question_numbers):
            raise HTTPException(status_code=422, detail=f"Media {media.id} tham chiếu câu không tồn tại")
    upload_ids = [item.upload_id for item in manifest.assets] + [
        item.upload_id for item in manifest.media
    ]
    if len(upload_ids) != len(set(upload_ids)):
        raise HTTPException(status_code=422, detail="Asset/media dùng trùng upload_id")


def _stat_declared_uploads(job: Job, manifest: ClientExtractionManifestV1) -> tuple[list[dict[str, Any]], int]:
    if storage is None:
        raise HTTPException(status_code=503, detail="Object storage chưa sẵn sàng")
    records = list((job.payload or {}).get("uploads") or [])
    by_id = {str(item.get("id")): item for item in records}
    required_ids = {str(item.upload_id) for item in manifest.assets}
    required_ids.update(str(item.upload_id) for item in manifest.media)
    source_records = [item for item in records if item.get("kind") == "source"]
    if len(source_records) != 1:
        raise HTTPException(status_code=409, detail="Session không có source PDF hợp lệ")
    required_ids.add(str(source_records[0]["id"]))
    checked: list[dict[str, Any]] = []
    for upload_id in required_ids:
        record = by_id.get(upload_id)
        if record is None:
            raise HTTPException(status_code=422, detail=f"Upload {upload_id} chưa được khai báo")
        manifest_asset = next(
            (item for item in manifest.assets if item.upload_id == upload_id), None
        )
        manifest_media = next(
            (item for item in manifest.media if item.upload_id == upload_id), None
        )
        declared = manifest_asset or manifest_media
        if declared is not None and (
            int(declared.size) != int(record["size"])
            or str(declared.content_type).lower()
            != str(record["content_type"]).lower()
        ):
            raise HTTPException(
                status_code=422,
                detail=f"Metadata manifest/upload {upload_id} không khớp",
            )
        try:
            stat = storage.client.stat_object(record["bucket"], storage.safe_key(record["object_key"]))
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"Object {upload_id} chưa upload hoặc không đọc được") from exc
        if int(stat.size or 0) != int(record["size"]):
            raise HTTPException(status_code=422, detail=f"Kích thước object {upload_id} không khớp policy")
        content_type = str(getattr(stat, "content_type", "") or "").split(";", 1)[0].lower()
        if content_type and content_type != str(record["content_type"]).lower():
            raise HTTPException(status_code=422, detail=f"MIME object {upload_id} không khớp policy")
        checked.append(record)

    source = source_records[0]
    if (
        int(source["size"]) != manifest.source_size
        or str(source["content_type"]).lower() != "application/pdf"
    ):
        raise HTTPException(status_code=422, detail="Metadata PDF nguồn không khớp manifest")
    with _PDF_VALIDATION_SLOTS:
        with tempfile.TemporaryDirectory(prefix="client-pdf-") as folder:
            path = Path(folder) / "source.pdf"
            storage.get_file(source["bucket"], source["object_key"], path)
            with path.open("rb") as handle:
                if handle.read(5) != b"%PDF-":
                    raise HTTPException(status_code=422, detail="Source object không có PDF magic")
                handle.seek(0)
                digest = hashlib.sha256()
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != manifest.source_sha256:
                raise HTTPException(status_code=422, detail="SHA-256 PDF nguồn không khớp manifest")
            try:
                with pdfplumber.open(path) as pdf:
                    page_count = len(pdf.pages)
            except Exception as exc:
                raise HTTPException(status_code=422, detail="Source PDF hỏng hoặc có mật khẩu") from exc
    if page_count < 1 or page_count > MAX_PAGES or page_count != manifest.page_count:
        raise HTTPException(status_code=422, detail="Số trang PDF không khớp manifest")
    return checked, page_count


def _legacy_payload(manifest: ClientExtractionManifestV1, uploads: list[dict[str, Any]]) -> dict[str, Any]:
    by_upload = {str(item["id"]): item for item in uploads}
    assets = {item.id: item for item in manifest.assets}
    answer_key = {int(number): letter.upper() for number, letter in manifest.answer_key.items()}
    questions = []
    for question in manifest.questions:
        item = question.model_dump(mode="json")
        if question.number in answer_key:
            item["correct"] = answer_key[question.number]
        questions.append(item)
    stimuli = []
    for stimulus in manifest.stimuli:
        item = stimulus.model_dump(mode="json")
        item["assets"] = [
            {
                **asset,
                "url": by_upload[assets[asset["id"]].upload_id]["object_key"],
            }
            for asset in item.get("assets") or []
        ]
        stimuli.append(item)
    audios = [
        {
            "id": media.id,
            "url": by_upload[media.upload_id]["object_key"],
            "filename": media.filename,
            "content_type": media.content_type,
            "size": media.size,
            "part": media.part,
            "scope": media.scope,
            "question_numbers": media.question_numbers,
            "group_id": media.group_id,
        }
        for media in manifest.media
    ]
    return {
        "schema_version": 2,
        "exam_type": manifest.exam_type,
        "requested_count": manifest.requested_count,
        "returned_count": len(questions),
        "questions": questions,
        "stimuli": stimuli,
        "issues": [item.model_dump(mode="json") for item in manifest.issues],
        "audio": audios[0] if len(audios) == 1 and audios[0]["scope"] == "full" else None,
        "audios": audios,
        "solutions": [item.model_dump(mode="json") for item in manifest.solutions],
        "metadata": {**manifest.metadata, "ingest_mode": "client_ocr", "source_sha256": manifest.source_sha256},
    }


@router.post("/client-extractions", status_code=201)
def create_client_extraction(body: ClientExtractionCreate, request: Request) -> dict[str, Any]:
    identity = require_roles(request, "teacher", "admin")
    owner_id = identity["user_id"]
    if storage is None:
        raise HTTPException(status_code=503, detail="Object storage chưa sẵn sàng")
    with session_scope() as session:
        existing = session.scalar(
            select(Job).where(Job.owner_user_id == owner_id, Job.client_request_id == body.client_request_id)
        )
        if existing is not None:
            payload = existing.payload or {}
            if payload.get("create_hash") != hashlib.sha256(body.model_dump_json().encode()).hexdigest():
                raise HTTPException(status_code=409, detail="client_request_id đã được dùng với payload khác")
            records = list(payload.get("uploads") or [])
            return {
                "id": existing.id,
                "reserved_exam_id": payload.get("reserved_exam_id"),
                "status": existing.status,
                "uploads": [_policy(item) for item in records],
                "expires_at": existing.expires_at,
            }
        session_id = str(uuid.uuid4())
        reserved_exam_id = str(uuid.uuid4())
        records = [
            _upload_record(item, reserved_exam_id=reserved_exam_id, session_id=session_id)
            for item in body.uploads
        ]
        source = next(item for item in records if item["kind"] == "source")
        row = Job(
            id=session_id,
            owner_user_id=owner_id,
            exam_type=body.component,
            filename=source["filename"],
            file_hash=body.source_sha256,
            pipeline_version=body.pipeline_version,
            status="uploading",
            progress=0,
            stage="client-upload",
            payload={
                "reserved_exam_id": reserved_exam_id,
                "requested_count": body.requested_count,
                "uploads": records,
                "create_hash": hashlib.sha256(body.model_dump_json().encode()).hexdigest(),
            },
            source_object_key=source["object_key"],
            ingest_mode="client_ocr",
            client_request_id=body.client_request_id,
            draft_revision=0,
            expires_at=utcnow() + timedelta(hours=SESSION_TTL_HOURS),
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="client_request_id vừa được sử dụng; hãy retry") from exc
        return {
            "id": row.id,
            "reserved_exam_id": reserved_exam_id,
            "status": row.status,
            "uploads": [_policy(item) for item in records],
            "expires_at": row.expires_at,
        }


@router.post("/client-extractions/{session_id}/uploads/refresh")
def refresh_client_uploads(session_id: str, body: RefreshUploadsRequest, request: Request) -> dict[str, Any]:
    identity = require_roles(request, "teacher", "admin")
    row = _job_for_owner(session_id, identity["user_id"])
    if row.status in {"committed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Session không còn nhận upload")
    records = {str(item["id"]): item for item in (row.payload or {}).get("uploads") or []}
    result = []
    for upload_id in dict.fromkeys(body.upload_ids):
        record = records.get(upload_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Upload {upload_id} không thuộc session")
        uploaded = False
        try:
            stat = storage.client.stat_object(record["bucket"], storage.safe_key(record["object_key"])) if storage else None
            uploaded = bool(stat and int(stat.size or 0) == int(record["size"]))
        except Exception:
            uploaded = False
        result.append({"upload_id": upload_id, "uploaded": uploaded, "policy": None if uploaded else _policy(record)})
    return {"id": row.id, "uploads": result}


@router.get("/client-extractions/{session_id}")
def get_client_extraction(session_id: str, request: Request) -> dict[str, Any]:
    identity = require_roles(request, "teacher", "admin")
    row = _job_for_owner(session_id, identity["user_id"])
    payload = row.payload or {}
    return {
        "id": row.id,
        "status": row.status,
        "draft_revision": row.draft_revision,
        "expires_at": row.expires_at,
        "exam_id": payload.get("exam_id"),
        "media": payload.get("media") or [],
    }


@router.delete("/client-extractions/{session_id}", status_code=202)
def delete_client_extraction(session_id: str, request: Request) -> dict[str, Any]:
    identity = require_roles(request, "teacher", "admin")
    with session_scope() as session:
        row = session.scalar(
            select(Job)
            .where(Job.id == session_id, Job.owner_user_id == identity["user_id"], Job.ingest_mode == "client_ocr")
            .with_for_update()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy client extraction session")
        if row.status != "committed":
            row.status = "cancelled"
            row.expires_at = utcnow()
            row.stage = "cleanup-pending"
        return {"id": row.id, "status": row.status}


@router.post("/client-extractions/{session_id}/commit")
def commit_client_extraction(session_id: str, body: ClientExtractionCommit, request: Request) -> dict[str, Any]:
    identity = require_roles(request, "teacher", "admin")
    owner_id = identity["user_id"]
    manifest_bytes = json.dumps(
        body.manifest.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise HTTPException(status_code=413, detail="Manifest vượt quá 5 MiB")
    manifest_hash = _canonical_hash(body.manifest)
    if not _SHA256.fullmatch(body.manifest_sha256.lower()) or manifest_hash != body.manifest_sha256.lower():
        raise HTTPException(status_code=422, detail="manifest_sha256 không khớp canonical manifest")
    _validate_manifest_structure(body.manifest)
    row = _job_for_owner(session_id, owner_id)
    if row.file_hash != body.manifest.source_sha256 or row.exam_type != body.manifest.exam_type:
        raise HTTPException(status_code=422, detail="Manifest không thuộc source/component của session")
    payload = row.payload or {}
    existing_commit_key = payload.get("commit_idempotency_key")
    if existing_commit_key and existing_commit_key != body.idempotency_key:
        raise HTTPException(status_code=409, detail="Session đã dùng idempotency key khác")
    if row.status == "committed":
        if row.manifest_hash != manifest_hash:
            raise HTTPException(status_code=409, detail="Session đã commit với manifest khác")
        return {"id": row.id, "status": row.status, "exam_id": payload.get("exam_id"), "idempotent": True}
    if row.manifest_hash and row.manifest_hash != manifest_hash:
        raise HTTPException(status_code=409, detail="Idempotency key/session đã dùng với manifest khác")

    # All MinIO stat/download/PDF validation finishes before the transaction.
    uploads, _ = _stat_declared_uploads(row, body.manifest)
    legacy_payload = _legacy_payload(body.manifest, uploads)
    source = next(item for item in uploads if item["kind"] == "source")
    upload_by_key = {str(item["object_key"]): item for item in uploads}
    media_state = []
    for media in legacy_payload.get("audios") or []:
        upload = upload_by_key[str(media["url"])]
        media_state.append(
            {
                "id": media["id"],
                "status": (
                    "ready"
                    if str(media.get("content_type") or "").lower() == "audio/mpeg"
                    else "pending"
                ),
                "bucket": upload["bucket"],
                "object_key": upload["object_key"],
                "content_type": media["content_type"],
                "size": media["size"],
                "error": None,
            }
        )
    with session_scope() as session:
        locked = session.scalar(
            select(Job)
            .where(Job.id == session_id, Job.owner_user_id == owner_id, Job.ingest_mode == "client_ocr")
            .with_for_update()
        )
        if locked is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy client extraction session")
        locked_payload = locked.payload or {}
        locked_commit_key = locked_payload.get("commit_idempotency_key")
        if locked_commit_key and locked_commit_key != body.idempotency_key:
            raise HTTPException(status_code=409, detail="Session đã dùng idempotency key khác")
        if locked.status == "committed":
            if locked.manifest_hash != manifest_hash:
                raise HTTPException(status_code=409, detail="Session đã commit với manifest khác")
            return {
                "id": locked.id,
                "status": locked.status,
                "exam_id": locked_payload.get("exam_id"),
                "idempotent": True,
            }
        if locked.status == "cancelled":
            raise HTTPException(status_code=409, detail="Session đã bị hủy")
        if locked.manifest_hash and locked.manifest_hash != manifest_hash:
            raise HTTPException(status_code=409, detail="Session đã dùng với manifest khác")

        locked.status = "committing"
        locked.manifest_hash = manifest_hash
        locked.stage = "persisting"
        locked.updated_at = utcnow()
        locked.payload = {
            **locked_payload,
            "commit_idempotency_key": body.idempotency_key,
        }
        reserved_exam_id = str(locked_payload["reserved_exam_id"])
        exam_id = persist_final_exam(
            legacy_payload,
            job_id=None,
            owner_user_id=owner_id,
            title=body.title,
            category=body.category,
            target_exam_id=body.target_exam_id,
            client_exam_id=reserved_exam_id if not body.target_exam_id else None,
            base_revision=body.base_revision,
            is_full_test_component=body.is_full_test_component,
            db_session=session,
        )
        if not exam_id:
            raise HTTPException(status_code=500, detail="Không persist được đề từ client manifest")
        exam = session.get(Exam, exam_id)
        if exam is None:
            raise HTTPException(status_code=500, detail="Exam commit không tồn tại")
        source_row = session.scalar(select(ExamSource).where(ExamSource.exam_id == exam_id, ExamSource.component == body.manifest.exam_type))
        if source_row is None:
            source_row = ExamSource(
                exam_id=exam_id,
                component=body.manifest.exam_type,
                bucket=source["bucket"],
                object_key=source["object_key"],
                filename=body.manifest.source_filename,
                content_type="application/pdf",
                size=body.manifest.source_size,
                sha256=body.manifest.source_sha256,
            )
            session.add(source_row)
        else:
            # Editing an existing exam is copy-on-write: the new immutable
            # source object replaces the prior reference only after the exam
            # transaction has persisted successfully.  Never copy or delete
            # the old MinIO object here; cleanup can reclaim it after the
            # revision retention window.
            source_row.bucket = source["bucket"]
            source_row.object_key = source["object_key"]
            source_row.filename = body.manifest.source_filename
            source_row.content_type = "application/pdf"
            source_row.size = body.manifest.source_size
            source_row.sha256 = body.manifest.source_sha256
        locked.status = "committed"
        locked.progress = 100
        locked.stage = "committed"
        locked.draft_revision += 1
        locked.expires_at = utcnow() + timedelta(days=7)
        locked.payload = {
            **(locked.payload or {}),
            "exam_id": exam_id,
            "committed_at": utcnow().isoformat(),
            "media": media_state,
        }
    pending_media = any(item["status"] == "pending" for item in media_state)
    if pending_media:
        try:
            from media_tasks import process_client_media

            if settings.use_celery:
                process_client_media.apply_async(args=[session_id], queue="media")
            else:
                process_client_media.run(session_id)
        except Exception:
            # The exam/draft is already durable. GET exposes pending media so a
            # retry/reconciliation job can safely resume without losing data.
            logger.exception("CLIENT_MEDIA_DISPATCH_FAILED session_id=%s", session_id)
    return {"id": session_id, "status": "committed", "exam_id": exam_id, "idempotent": False}


@router.post("/solution-imports/validate")
def validate_client_solution_rows(body: SolutionRowsRequest, request: Request) -> dict[str, Any]:
    require_roles(request, "teacher", "admin")
    try:
        rows = validate_solutions([item.model_dump(mode="json") for item in body.rows], body.exam_type)
    except SolutionValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.issues) from exc
    return {"valid": True, "rows": rows, "count": len(rows)}
