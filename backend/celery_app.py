"""Celery application for bounded media, document and maintenance jobs."""

from celery import Celery

from config import settings


celery_app = Celery(
    "smart_exam_converter",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["media_tasks", "maintenance_tasks", "solution_tasks", "ocr_tasks"],
)
celery_app.conf.update(
    task_default_queue="documents",
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    task_soft_time_limit=5 * 60,
    task_time_limit=6 * 60,
    result_expires=24 * 60 * 60,
    beat_schedule={
        "finalize-expired-class-attempts": {
            "task": "maintenance.finalize_expired_class_attempts",
            "schedule": 30.0,
            "options": {"queue": "maintenance"},
        },
        "purge-attempt-sync-batches": {
            "task": "maintenance.purge_attempt_sync_batches",
            "schedule": 60 * 60.0,
            "options": {"queue": "maintenance"},
        },
        "purge-solution-imports": {
            "task": "maintenance.purge_solution_imports",
            "schedule": 60 * 60.0,
            "options": {"queue": "maintenance"},
        },
        "purge-expired-edit-sessions": {
            "task": "maintenance.purge_expired_edit_sessions",
            "schedule": 10 * 60.0,
            "options": {"queue": "maintenance"},
        },
        "purge-full-test-components": {
            "task": "maintenance.purge_full_test_components",
            "schedule": 10 * 60.0,
            "options": {"queue": "maintenance"},
        },
        "purge-expired-jobs": {
            "task": "maintenance.purge_expired_jobs",
            "schedule": 60 * 60.0,
            "options": {"queue": "maintenance"},
        },
    },
)
