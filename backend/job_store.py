"""Small filesystem-backed job store.

The job state is stored as JSON so polling still works when uvicorn uses more
than one worker. Runtime files expire automatically and are never placed in the
repository.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any


JOB_TTL_SECONDS = int(os.getenv("TOOL_TAO_DE_JOB_TTL", str(24 * 60 * 60)))
# Bump this whenever extraction/cropping semantics change.  Reusing a draft
# produced by an older cropper would otherwise make uploading the same PDF look
# as if the new pipeline never ran.
# v3.5 keeps physical page 1 after the caller trims the cover/directions and
# namespaces shared titles by tag. 3.5.1 additionally fixes Reading sequence
# jumps caused by passage numbers and re-runs pages with incomplete text layers;
# older cached jobs must not be reused because they can contain missing 153–195.
#
# ``jobs.pipeline_version`` is deliberately VARCHAR(40) in existing
# deployments, so this durable cache key must remain within that boundary.
# Keeping the cache version compact prevents an upload from failing with a
# database 500 before the OCR job is even queued.
PIPELINE_CACHE_VERSION = "3.5.1-rc-ocr-sequence"
if len(PIPELINE_CACHE_VERSION) > 40:  # defensive guard for future revisions
    raise RuntimeError("PIPELINE_CACHE_VERSION vượt quá giới hạn cột jobs")
DATA_DIR = Path(
    os.getenv(
        "TOOL_TAO_DE_DATA_DIR",
        str(Path(tempfile.gettempdir()) / "tool-tao-de" / "jobs"),
    )
).resolve()


def cache_file_requirements(
    state: dict[str, Any],
) -> tuple[set[str], set[str], set[str]] | None:
    """Return the files a cached review draft needs in order to remain usable.

    A review needs both its rendered crops and the corresponding source pages:
    the latter are opened by the crop editor.  Returning ``None`` denotes an
    unsafe/malformed reference rather than a draft with no media.
    """

    asset_ids: set[str] = set()
    page_names: set[str] = set()
    audio_ids: set[str] = set()

    for stimulus in state.get("stimuli") or []:
        if not isinstance(stimulus, dict):
            return None
        for asset in stimulus.get("assets") or []:
            if not isinstance(asset, dict):
                return None
            asset_id = str(asset.get("id") or "")
            if not asset_id or Path(asset_id).name != asset_id:
                return None
            try:
                page_number = int(asset.get("page"))
            except (TypeError, ValueError):
                return None
            if page_number < 1 or page_number > 500:
                return None
            asset_ids.add(asset_id)
            page_names.add(f"page-{page_number:03d}.jpg")

    audio_refs = state.get("audios") or []
    if not audio_refs and state.get("audio"):
        audio_refs = [state["audio"]]
    for audio in audio_refs:
        if not isinstance(audio, dict):
            return None
        audio_id = str(audio.get("id") or "")
        if not audio_id or Path(audio_id).name != audio_id:
            return None
        audio_ids.add(audio_id)

    return asset_ids, page_names, audio_ids


def local_cache_files_available(job_dir: Path, state: dict[str, Any]) -> bool:
    requirements = cache_file_requirements(state)
    if requirements is None:
        return False
    asset_ids, page_names, audio_ids = requirements
    for folder, names in (
        ("assets", asset_ids),
        ("pages", page_names),
        ("audio", audio_ids),
    ):
        for name in names:
            path = job_dir / folder / name
            try:
                if not path.is_file() or path.stat().st_size <= 0:
                    return False
            except FileNotFoundError:
                return False
    return True


class JobStore:
    def __init__(self, root: Path = DATA_DIR) -> None:
        # Windows may return equivalent long/short paths from ``resolve()``.
        # Keep the root canonical too so the traversal guard does not reject
        # a valid job directory created by this store.
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def cleanup(self) -> None:
        now = time.time()
        with self._lock:
            for child in self.root.iterdir():
                if not child.is_dir():
                    continue
                try:
                    if now - child.stat().st_mtime > JOB_TTL_SECONDS:
                        shutil.rmtree(child, ignore_errors=True)
                except FileNotFoundError:
                    continue

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
        job_dir = self.root / job_id
        (job_dir / "assets").mkdir(parents=True)
        (job_dir / "pages").mkdir()
        (job_dir / "audio").mkdir()
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
        self.write(job_id, state)
        return job_id, job_dir

    def _safe_job_dir(self, job_id: str) -> Path:
        try:
            normalized = str(uuid.UUID(job_id))
        except ValueError as exc:
            raise FileNotFoundError("Job không hợp lệ") from exc
        path = (self.root / normalized).resolve()
        if path.parent != self.root or not path.is_dir():
            raise FileNotFoundError("Không tìm thấy job")
        return path

    def job_dir(self, job_id: str) -> Path:
        return self._safe_job_dir(job_id)

    def read(self, job_id: str) -> dict[str, Any]:
        path = self._safe_job_dir(job_id) / "state.json"
        with self._lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def write(self, job_id: str, state: dict[str, Any]) -> None:
        job_dir = self.root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        state.setdefault("metadata", {})["updated_at"] = time.time()
        target = job_dir / "state.json"
        temp = job_dir / f".state-{uuid.uuid4().hex}.tmp"
        payload = json.dumps(state, ensure_ascii=False, indent=2)
        with self._lock:
            temp.write_text(payload, encoding="utf-8")
            os.replace(temp, target)

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        state = self.read(job_id)
        state.update(changes)
        self.write(job_id, state)
        return state

    def write_progress(self, job_id: str, **changes: Any) -> dict[str, Any]:
        """Persist lightweight job progress without changing media semantics."""
        with self._lock:
            state = self.read(job_id)
            state.update(changes)
            self.write(job_id, state)
            return state

    def update_media(
        self,
        job_id: str,
        *,
        audios: list[dict[str, Any]],
        audio: dict[str, Any] | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically merge completed audio into the latest OCR progress state."""
        with self._lock:
            state = self.read(job_id)
            state["audios"] = audios
            state["audio"] = audio
            state.setdefault("metadata", {}).update(metadata)
            self.write(job_id, state)
            return state

    def find_cached(
        self,
        *,
        file_hash: str,
        exam_type: str,
        owner_user_id: str | None = None,
    ) -> dict[str, Any] | None:
        self.cleanup()
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            try:
                state = json.loads((child / "state.json").read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            metadata = state.get("metadata", {})
            if (
                metadata.get("file_hash") == file_hash
                and metadata.get("pipeline_version") == PIPELINE_CACHE_VERSION
                and state.get("exam_type") == exam_type
                and state.get("status") in {"review", "ready"}
                and state.get("job_id") == child.name
                and local_cache_files_available(child, state)
                and (
                    owner_user_id is None
                    or metadata.get("owner_user_id") == owner_user_id
                )
            ):
                state["cached"] = True
                self.write(state["job_id"], state)
                return state
        return None

    def owner_id(self, job_id: str) -> str | None:
        state = self.read(job_id)
        owner_id = (state.get("metadata") or {}).get("owner_user_id")
        return str(owner_id) if owner_id else None

    def asset_path(self, job_id: str, asset_id: str) -> Path:
        job_dir = self._safe_job_dir(job_id)
        if not asset_id or Path(asset_id).name != asset_id:
            raise FileNotFoundError("Asset không hợp lệ")
        path = (job_dir / "assets" / asset_id).resolve()
        if path.parent != (job_dir / "assets").resolve() or not path.is_file():
            raise FileNotFoundError("Không tìm thấy asset")
        return path

    def audio_path(self, job_id: str, audio_id: str) -> Path:
        job_dir = self._safe_job_dir(job_id)
        if not audio_id or Path(audio_id).name != audio_id:
            raise FileNotFoundError("Audio không hợp lệ")
        audio_dir = (job_dir / "audio").resolve()
        path = (audio_dir / audio_id).resolve()
        if path.parent != audio_dir or not path.is_file():
            raise FileNotFoundError("Không tìm thấy audio")
        return path


def _build_store() -> JobStore:
    # Imports stay lazy so the OCR unit tests do not require PostgreSQL/MinIO.
    from config import settings

    if settings.desktop and settings.desktop_data_dir:
        return JobStore(Path(settings.desktop_data_dir).resolve() / "jobs")
    if settings.persistence_enabled:
        from persistent_job_store import PersistentJobStore

        return PersistentJobStore()  # type: ignore[return-value]
    return JobStore()


store = _build_store()
