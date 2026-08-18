"""Bounded post-commit media normalization; this module never performs OCR."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from celery_app import celery_app
from database import session_scope
from models import Asset, Exam, ExamVersionAsset, Job, utcnow
from object_storage import storage
from sqlalchemy import select, update


WEB_AUDIO_TYPES = {"audio/mpeg"}


def _media_state(job: Job) -> list[dict[str, Any]]:
    return [dict(item) for item in (job.payload or {}).get("media") or []]


def _set_media_status(
    job_id: str,
    media_id: str,
    status: str,
    *,
    object_key: str | None = None,
    size: int | None = None,
    error: str | None = None,
) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        payload = dict(job.payload or {})
        media = _media_state(job)
        for item in media:
            if item.get("id") != media_id:
                continue
            item["status"] = status
            if object_key is not None:
                item["object_key"] = object_key
            if size is not None:
                item["size"] = size
            item["error"] = error
            break
        payload["media"] = media
        job.payload = payload
        job.updated_at = utcnow()


@celery_app.task(
    bind=True,
    name="media.process_client_extraction",
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
    soft_time_limit=5 * 60,
    time_limit=6 * 60,
)
def process_client_media(self, job_id: str) -> dict[str, object]:
    if storage is None:
        raise RuntimeError("MinIO chưa sẵn sàng cho media worker")
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return {"id": job_id, "status": "missing"}
        exam_id = str((job.payload or {}).get("exam_id") or "")
        media = _media_state(job)

    completed = 0
    failed = 0
    for item in media:
        media_id = str(item.get("id") or "")
        source_key = str(item.get("object_key") or "")
        source_bucket = str(item.get("bucket") or "")
        content_type = str(item.get("content_type") or "").lower()
        if not media_id or not source_key or not source_bucket:
            failed += 1
            continue
        if content_type in WEB_AUDIO_TYPES:
            _set_media_status(job_id, media_id, "ready")
            completed += 1
            continue

        _set_media_status(job_id, media_id, "processing")
        destination = f"{source_key.rsplit('/', 1)[0]}/normalized/{media_id}.mp3"
        try:
            with tempfile.TemporaryDirectory(prefix="client-media-") as directory:
                source = Path(directory) / "source"
                output = Path(directory) / "output.mp3"
                storage.get_file(source_bucket, source_key, source)
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-nostdin",
                        "-y",
                        "-i",
                        str(source),
                        "-vn",
                        "-codec:a",
                        "libmp3lame",
                        "-b:a",
                        "128k",
                        "-threads",
                        "1",
                        str(output),
                    ],
                    check=True,
                    timeout=240,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                size = output.stat().st_size
                storage.put_file(source_bucket, destination, output, "audio/mpeg")

            with session_scope() as session:
                session.execute(
                    update(Asset)
                    .where(
                        Asset.exam_id == exam_id,
                        Asset.kind == "audio",
                        Asset.filename == media_id,
                    )
                    .values(
                        object_key=destination,
                        content_type="audio/mpeg",
                        size=size,
                    )
                )
                session.execute(
                    update(ExamVersionAsset)
                    .where(ExamVersionAsset.object_key == source_key)
                    .values(
                        object_key=destination,
                        content_type="audio/mpeg",
                        size=size,
                    )
                )
                exam = session.get(Exam, exam_id)
                if exam is not None:
                    payload = dict(exam.payload or {})
                    audios = [dict(audio) for audio in payload.get("audios") or []]
                    for audio in audios:
                        if str(audio.get("id") or "") == media_id:
                            audio.update(
                                url=destination,
                                content_type="audio/mpeg",
                                size=size,
                            )
                    payload["audios"] = audios
                    if str((payload.get("audio") or {}).get("id") or "") == media_id:
                        payload["audio"] = next(
                            (audio for audio in audios if audio.get("id") == media_id),
                            payload.get("audio"),
                        )
                    exam.payload = payload
            _set_media_status(
                job_id,
                media_id,
                "ready",
                object_key=destination,
                size=size,
            )
            completed += 1
        except Exception as exc:
            _set_media_status(job_id, media_id, "failed", error=str(exc)[:500])
            failed += 1
    return {"id": job_id, "status": "completed", "ready": completed, "failed": failed}
