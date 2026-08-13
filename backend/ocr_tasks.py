"""Heavy OCR tasks. Each worker uses disposable local working directories."""

from __future__ import annotations

from celery_app import celery_app
from job_store import store
from main import _run_extraction_job


@celery_app.task(
    bind=True,
    name="ocr.process_extraction",
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def process_extraction(self, job_id: str) -> dict[str, str]:
    store.cleanup()
    try:
        input_path = store.job_dir(job_id) / "input.pdf"
        if not input_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy PDF nguồn của job {job_id}")
        _run_extraction_job(job_id, input_path)
        return {"job_id": job_id, "status": store.read(job_id)["status"]}
    except Exception as exc:
        # Never leave a consumed task forever at queued/0%.  _run_extraction
        # already records pipeline failures, while bootstrap/materialization
        # errors are recorded here.
        try:
            state = store.read(job_id)
            if state.get("status") in {"queued", "processing"}:
                state.update(
                    {
                        "status": "failed",
                        "stage": "Worker xử lý thất bại",
                        "error": str(exc),
                    }
                )
                store.write(job_id, state)
        except Exception:
            pass
        raise
    finally:
        if hasattr(store, "evict_local"):
            store.evict_local(job_id)
