"""Bounded Celery task for solution document parsing outside exam hot paths."""

from __future__ import annotations

from pathlib import Path
import tempfile

from celery_app import celery_app
from database import session_scope
from models import SolutionImport, utcnow
from object_storage import storage
from solution_importer import parse_solution_file


@celery_app.task(
    bind=True,
    name="solutions.process_import",
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
    soft_time_limit=5 * 60,
    time_limit=6 * 60,
)
def process_solution_import(self, import_id: str) -> dict[str, object]:
    with session_scope() as session:
        row = session.get(SolutionImport, import_id)
        if row is None:
            return {"id": import_id, "status": "missing"}
        row.status = "processing"
        bucket, object_key, filename, exam_type = (
            row.bucket,
            row.object_key,
            row.filename,
            row.exam_type,
        )
    if storage is None or not bucket or not object_key:
        raise RuntimeError("MinIO chưa sẵn sàng cho solution import")
    try:
        with tempfile.TemporaryDirectory(prefix="solution-import-") as directory:
            path = Path(directory) / Path(filename).name
            storage.get_file(bucket, object_key, path)
            result = parse_solution_file(path, exam_type)
        with session_scope() as session:
            row = session.get(SolutionImport, import_id)
            if row is not None:
                row.status = "completed"
                row.result = result
                row.issues = result.get("issues") or []
                row.error = None
                row.updated_at = utcnow()
        return {"id": import_id, "status": "completed"}
    except Exception as exc:
        with session_scope() as session:
            row = session.get(SolutionImport, import_id)
            if row is not None:
                row.status = "failed"
                row.error = str(exc)[:4000]
                row.updated_at = utcnow()
        raise
