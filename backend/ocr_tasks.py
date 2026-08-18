"""Bounded server-side Tesseract extraction task."""

from __future__ import annotations

from celery_app import celery_app


@celery_app.task(
    bind=True,
    name="ocr.process_extraction",
    acks_late=True,
    track_started=True,
    soft_time_limit=15 * 60,
    time_limit=20 * 60,
)
def process_extraction(self, job_id: str) -> None:
    """Materialize one job and run the canonical OCR/audio pipeline.

    Production runs this queue with one worker process. Page-level parallelism
    remains capped inside the pipeline so Tesseract does not exhaust RAM.
    """

    from job_store import store
    from main import _run_extraction_job

    job_dir = store.job_dir(job_id)
    _run_extraction_job(job_id, job_dir / "input.pdf")
