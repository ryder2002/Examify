"""PostgreSQL/MinIO implementation of the extraction job store.

Local directories are disposable working caches. PostgreSQL and MinIO remain
the source of truth, so API and worker containers can be restarted or scaled.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from config import settings
from database import session_scope
from job_store import (
    JOB_TTL_SECONDS,
    PIPELINE_CACHE_VERSION,
    cache_file_requirements,
)
from models import Job
from object_storage import storage


class PersistentJobStore:
    def __init__(self) -> None:
        if storage is None:
            raise RuntimeError("MinIO chưa được cấu hình")
        self.root = Path(
            os.getenv(
                "TOOL_TAO_DE_WORK_DIR",
                str(Path(tempfile.gettempdir()) / "smart-exam-worker"),
            )
        ).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._uploaded_signatures: dict[tuple[str, str], tuple[int, int]] = {}

    def cleanup(self) -> None:
        cutoff = time.time() - JOB_TTL_SECONDS
        stale_staging_cutoff = time.time() - max(
            600,
            int(os.getenv("TOOL_TAO_DE_STAGING_TTL_SECONDS", "3600")),
        )
        # This budget applies only to disposable API caches. The OCR worker
        # sets it to zero because its two active processing directories are
        # already bounded by Celery concurrency and the worker tmpfs limit.
        cache_budget = max(
            0,
            int(os.getenv("TOOL_TAO_DE_LOCAL_CACHE_MAX_BYTES", "0")),
        )
        eviction_min_age = max(
            60,
            int(os.getenv("TOOL_TAO_DE_CACHE_EVICT_MIN_AGE_SECONDS", "900")),
        )
        now = time.time()
        cache_entries: list[tuple[float, int, Path]] = []
        for child in self.root.iterdir():
            if child.is_file():
                try:
                    if (
                        child.name.startswith((".upload-", ".audio-upload-"))
                        and child.stat().st_mtime < stale_staging_cutoff
                    ):
                        child.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass
                continue
            if not child.is_dir():
                continue
            try:
                modified_at = child.stat().st_mtime
                if modified_at < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    continue
                if cache_budget:
                    size = sum(
                        path.stat().st_size
                        for path in child.rglob("*")
                        if path.is_file()
                    )
                    cache_entries.append((modified_at, size, child))
            except FileNotFoundError:
                continue
        if cache_budget:
            total = sum(size for _modified, size, _path in cache_entries)
            for modified_at, size, child in sorted(cache_entries):
                if total <= cache_budget:
                    break
                # Never evict a directory that may still be in an edit/crop
                # request. Recent cache may temporarily exceed the soft budget;
                # the SSD scratch/readiness guard remains the hard protection.
                if now - modified_at < eviction_min_age:
                    continue
                shutil.rmtree(child, ignore_errors=True)
                total = max(0, total - size)

    def discard(self, job_id: str) -> None:
        """Rollback a job that failed before it was handed to Celery."""

        normalized = self._validate_id(job_id)
        cleanup_succeeded = True
        for bucket in {
            settings.minio_bucket_sources,
            settings.minio_bucket_assets,
            settings.minio_bucket_audio,
        }:
            try:
                storage.remove_prefix(bucket, f"jobs/{normalized}/")
            except Exception:
                # Keep a discoverable row when object cleanup is incomplete so
                # the bounded retention task can retry instead of leaking an
                # unreachable MinIO prefix forever.
                cleanup_succeeded = False
        with session_scope() as session:
            if cleanup_succeeded:
                session.execute(delete(Job).where(Job.id == normalized))
            else:
                row = session.get(Job, normalized)
                if row is not None:
                    row.status = "failed"
                    row.stage = "Dọn dữ liệu tải lên chưa hoàn tất"
                    row.error = "MinIO cleanup pending"
        self.evict_local(normalized)

    def create(
        self,
        *,
        filename: str,
        exam_type: str,
        file_hash: str,
        owner_user_id: str | None = None,
    ) -> tuple[str, Path]:
        self.cleanup()
        job_id = str(uuid.uuid4())
        job_dir = self._create_local(job_id)
        state = {
            "schema_version": 2,
            "job_id": job_id,
            "exam_type": exam_type,
            "status": "queued",
            "stage": "Đang chờ xử lý",
            "progress": 0,
            "processing_phase": "queued",
            "phase_progress": 0,
            "audio_progress": 0,
            "ocr_progress": 0,
            "audio_stage": "Đang chờ xử lý audio",
            "ocr_stage": "Đang chờ OCR",
            "filename": filename,
            "requested_count": None,
            "returned_count": 0,
            "questions": [],
            "stimuli": [],
            "issues": [],
            "error": None,
            "cached": False,
            "audio": None,
            "audios": [],
            "solutions": [],
            "metadata": {
                "file_hash": file_hash,
                "pipeline_version": PIPELINE_CACHE_VERSION,
                "created_at": time.time(),
                "updated_at": time.time(),
                "owner_user_id": owner_user_id,
            },
        }
        with session_scope() as session:
            session.add(
                Job(
                    id=job_id,
                    owner_user_id=owner_user_id,
                    exam_type=exam_type,
                    filename=filename,
                    file_hash=file_hash,
                    pipeline_version=PIPELINE_CACHE_VERSION,
                    status="queued",
                    progress=0,
                    stage=state["stage"],
                    payload=state,
                    source_object_key=f"jobs/{job_id}/input.pdf",
                )
            )
        return job_id, job_dir

    def set_owner(self, job_id: str, user_id: str | None) -> None:
        if not user_id:
            return
        with session_scope() as session:
            row = session.get(Job, job_id)
            if row:
                row.owner_user_id = user_id

    def _create_local(self, job_id: str) -> Path:
        self._validate_id(job_id)
        job_dir = self.root / job_id
        (job_dir / "assets").mkdir(parents=True, exist_ok=True)
        (job_dir / "pages").mkdir(parents=True, exist_ok=True)
        (job_dir / "audio").mkdir(parents=True, exist_ok=True)
        return job_dir

    @staticmethod
    def _validate_id(job_id: str) -> str:
        try:
            return str(uuid.UUID(job_id))
        except ValueError as exc:
            raise FileNotFoundError("Job không hợp lệ") from exc

    def job_dir(self, job_id: str) -> Path:
        normalized = self._validate_id(job_id)
        job_dir = self._local_cache_dir(normalized)
        self._materialize(normalized, job_dir)
        return job_dir

    def _local_cache_dir(self, job_id: str) -> Path:
        normalized = self._validate_id(job_id)
        with session_scope() as session:
            if session.get(Job, normalized) is None:
                raise FileNotFoundError("Không tìm thấy job")
        return self._create_local(normalized)

    def read(self, job_id: str) -> dict[str, Any]:
        normalized = self._validate_id(job_id)
        with session_scope() as session:
            row = session.get(Job, normalized)
            if row is None:
                raise FileNotFoundError("Không tìm thấy job")
            # Detached JSON values must not be mutated behind SQLAlchemy's back.
            return json.loads(json.dumps(row.payload, ensure_ascii=False))

    def write(self, job_id: str, state: dict[str, Any]) -> None:
        normalized = self._validate_id(job_id)
        state.setdefault("metadata", {})["updated_at"] = time.time()
        self._sync_local(normalized)
        with session_scope() as session:
            row = session.get(Job, normalized)
            if row is None:
                raise FileNotFoundError("Không tìm thấy job")
            row.status = str(state.get("status", row.status))
            row.progress = int(state.get("progress", row.progress))
            row.stage = str(state.get("stage", row.stage))
            row.error = state.get("error")
            row.payload = json.loads(json.dumps(state, ensure_ascii=False))

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        state = self.read(job_id)
        state.update(changes)
        self.write(job_id, state)
        return state

    def write_progress(self, job_id: str, **changes: Any) -> dict[str, Any]:
        """Update PostgreSQL polling state without scanning/uploading media.

        Audio progress is emitted while FFmpeg is still creating files. The
        completed manifest is synced once by ``write()``; progress milestones
        must not repeatedly walk the growing audio directory or upload partial
        job output.
        """

        normalized = self._validate_id(job_id)
        with session_scope() as session:
            row = session.scalar(
                select(Job).where(Job.id == normalized).with_for_update()
            )
            if row is None:
                raise FileNotFoundError("Không tìm thấy job")
            state = json.loads(json.dumps(row.payload, ensure_ascii=False))
            state.update(changes)
            state.setdefault("metadata", {})["updated_at"] = time.time()
            row.status = str(state.get("status", row.status))
            row.progress = int(state.get("progress", row.progress))
            row.stage = str(state.get("stage", row.stage))
            row.error = state.get("error")
            row.payload = json.loads(json.dumps(state, ensure_ascii=False))
        return state

    def update_media(
        self,
        job_id: str,
        *,
        audios: list[dict[str, Any]],
        audio: dict[str, Any] | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Upload completed audio and merge it under a PostgreSQL row lock."""
        normalized = self._validate_id(job_id)
        audio_dir = self.root / normalized / "audio"
        # Upload only files that remain in the completed manifest.  Audio
        # Full may leave its 40+ MB source next to 55 generated clips; walking
        # the whole directory re-uploaded that source even though the API had
        # already persisted it before the worker began processing.
        audio_ids = {
            str(item.get("id") or "")
            for item in [*audios, *([audio] if audio is not None else [])]
        }
        if audio_dir.is_dir():
            for audio_id in sorted(audio_ids):
                if not audio_id or Path(audio_id).name != audio_id:
                    continue
                path = audio_dir / audio_id
                if path.is_file():
                    self._put_changed(
                        settings.minio_bucket_audio,
                        f"jobs/{normalized}/audio/{audio_id}",
                        path,
                    )
        with session_scope() as session:
            row = session.scalar(
                select(Job).where(Job.id == normalized).with_for_update()
            )
            if row is None:
                raise FileNotFoundError("Không tìm thấy job")
            state = json.loads(json.dumps(row.payload, ensure_ascii=False))
            state["audios"] = audios
            state["audio"] = audio
            state.setdefault("metadata", {}).update(metadata)
            state["metadata"]["updated_at"] = time.time()
            row.payload = json.loads(json.dumps(state, ensure_ascii=False))
        return state

    def find_cached(
        self,
        *,
        file_hash: str,
        exam_type: str,
        owner_user_id: str | None = None,
    ) -> dict[str, Any] | None:
        with session_scope() as session:
            query = select(Job).where(
                Job.file_hash == file_hash,
                Job.exam_type == exam_type,
                Job.pipeline_version == PIPELINE_CACHE_VERSION,
                Job.status.in_(["review", "ready"]),
            )
            if owner_user_id:
                query = query.where(Job.owner_user_id == owner_user_id)
            rows = list(
                session.scalars(query.order_by(Job.updated_at.desc()).limit(20))
            )
            candidates = [
                (
                    row.id,
                    json.loads(json.dumps(row.payload, ensure_ascii=False)),
                )
                for row in rows
            ]

        for job_id, state in candidates:
            if not self._cached_objects_available(job_id, state):
                continue
            state["cached"] = True
            with session_scope() as session:
                row = session.get(Job, job_id)
                if row is None:
                    continue
                row.payload = state
            return state
        return None

    def _cached_objects_available(
        self, job_id: str, state: dict[str, Any]
    ) -> bool:
        """Reject metadata-only cache hits whose private media was removed."""

        if state.get("job_id") != job_id:
            return False
        requirements = cache_file_requirements(state)
        if requirements is None:
            return False
        asset_ids, page_names, audio_ids = requirements
        try:
            asset_keys = set(
                storage.list_prefix(
                    settings.minio_bucket_assets, f"jobs/{job_id}/assets/"
                )
            )
            page_keys = set(
                storage.list_prefix(
                    settings.minio_bucket_assets, f"jobs/{job_id}/pages/"
                )
            )
            audio_keys = set(
                storage.list_prefix(
                    settings.minio_bucket_audio, f"jobs/{job_id}/audio/"
                )
            )
        except Exception:
            return False
        return (
            {f"jobs/{job_id}/assets/{name}" for name in asset_ids} <= asset_keys
            and {f"jobs/{job_id}/pages/{name}" for name in page_names} <= page_keys
            and {f"jobs/{job_id}/audio/{name}" for name in audio_ids} <= audio_keys
        )

    def owner_id(self, job_id: str) -> str | None:
        with session_scope() as session:
            row = session.get(Job, self._validate_id(job_id))
            if row is None:
                raise FileNotFoundError("Không tìm thấy job")
            return row.owner_user_id

    def evict_local(self, job_id: str) -> None:
        normalized = self._validate_id(job_id)
        shutil.rmtree(self.root / normalized, ignore_errors=True)

    def asset_path(self, job_id: str, asset_id: str) -> Path:
        if not asset_id or Path(asset_id).name != asset_id:
            raise FileNotFoundError("Asset không hợp lệ")
        job_dir = self._local_cache_dir(job_id)
        path = (job_dir / "assets" / asset_id).resolve()
        if path.parent != (job_dir / "assets").resolve():
            raise FileNotFoundError("Asset không hợp lệ")
        if not path.is_file() or path.stat().st_size <= 0:
            path.unlink(missing_ok=True)
            try:
                storage.get_file(
                    settings.minio_bucket_assets,
                    f"jobs/{job_id}/assets/{asset_id}",
                    path,
                )
            except Exception as exc:
                raise FileNotFoundError("Không tìm thấy asset") from exc
        return path

    def audio_path(self, job_id: str, audio_id: str) -> Path:
        if not audio_id or Path(audio_id).name != audio_id:
            raise FileNotFoundError("Audio không hợp lệ")
        job_dir = self._local_cache_dir(job_id)
        path = (job_dir / "audio" / audio_id).resolve()
        if path.parent != (job_dir / "audio").resolve():
            raise FileNotFoundError("Audio không hợp lệ")
        if not path.is_file() or path.stat().st_size <= 0:
            path.unlink(missing_ok=True)
            try:
                storage.get_file(
                    settings.minio_bucket_audio,
                    f"jobs/{job_id}/audio/{audio_id}",
                    path,
                )
            except Exception as exc:
                raise FileNotFoundError("Không tìm thấy audio") from exc
        return path

    def page_path(self, job_id: str, page_number: int) -> Path:
        filename = f"page-{page_number:03d}.jpg"
        job_dir = self._local_cache_dir(job_id)
        path = job_dir / "pages" / filename
        if not path.is_file() or path.stat().st_size <= 0:
            path.unlink(missing_ok=True)
            try:
                storage.get_file(
                    settings.minio_bucket_assets,
                    f"jobs/{job_id}/pages/{filename}",
                    path,
                )
            except Exception as exc:
                raise FileNotFoundError("Không tìm thấy trang") from exc
        return path

    def _sync_local(self, job_id: str) -> None:
        job_dir = self.root / job_id
        if not job_dir.is_dir():
            return
        input_path = job_dir / "input.pdf"
        if input_path.is_file():
            self._put_changed(
                settings.minio_bucket_sources,
                f"jobs/{job_id}/input.pdf",
                input_path,
                "application/pdf",
            )
        for folder, bucket in (
            ("assets", settings.minio_bucket_assets),
            ("pages", settings.minio_bucket_assets),
            ("audio", settings.minio_bucket_audio),
        ):
            directory = job_dir / folder
            if not directory.is_dir():
                continue
            for path in directory.iterdir():
                if path.is_file() and not path.name.startswith("."):
                    self._put_changed(
                        bucket, f"jobs/{job_id}/{folder}/{path.name}", path
                    )

    def _put_changed(
        self,
        bucket: str,
        key: str,
        path: Path,
        content_type: str | None = None,
    ) -> None:
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        cache_key = (bucket, key)
        if self._uploaded_signatures.get(cache_key) == signature:
            return
        storage.put_file(bucket, key, path, content_type)
        self._uploaded_signatures[cache_key] = signature

    def _materialize(self, job_id: str, job_dir: Path) -> None:
        input_path = job_dir / "input.pdf"
        if not input_path.is_file():
            try:
                storage.get_file(
                    settings.minio_bucket_sources,
                    f"jobs/{job_id}/input.pdf",
                    input_path,
                )
                self._remember_download(
                    settings.minio_bucket_sources,
                    f"jobs/{job_id}/input.pdf",
                    input_path,
                )
            except Exception:
                pass
        for folder, bucket in (
            ("assets", settings.minio_bucket_assets),
            ("pages", settings.minio_bucket_assets),
            ("audio", settings.minio_bucket_audio),
        ):
            prefix = f"jobs/{job_id}/{folder}/"
            try:
                keys = storage.list_prefix(bucket, prefix)
            except Exception:
                keys = []
            for key in keys:
                destination = job_dir / folder / Path(key).name
                if not destination.is_file():
                    storage.get_file(bucket, key, destination)
                    self._remember_download(bucket, key, destination)

    def _remember_download(self, bucket: str, key: str, path: Path) -> None:
        """Mark a MinIO-materialized file as already durable in this worker."""

        try:
            stat = path.stat()
        except FileNotFoundError:
            return
        self._uploaded_signatures[(bucket, key)] = (stat.st_size, stat.st_mtime_ns)
