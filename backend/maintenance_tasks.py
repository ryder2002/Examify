"""Small periodic maintenance jobs with bounded transaction sizes."""

from datetime import timedelta

from sqlalchemy import delete, select, update

from celery_app import celery_app
from classroom_api import finalize_expired_class_attempts
from config import settings
from database import session_scope
from full_test_components import abandon_pending_components
from models import (
    Asset,
    AnswerKey,
    AttemptSyncBatch,
    Exam,
    ExamEditSession,
    ExamSource,
    ExamVersion,
    Job,
    SolutionImport,
    utcnow,
)
from object_storage import storage


@celery_app.task(name="maintenance.finalize_expired_class_attempts")
def finalize_class_attempts() -> dict[str, int]:
    return {"finalized": finalize_expired_class_attempts()}


@celery_app.task(name="maintenance.purge_attempt_sync_batches")
def purge_attempt_sync_batches(limit: int = 5_000) -> dict[str, int]:
    """Bound the idempotency ledger without touching active exam data."""

    cutoff = utcnow() - timedelta(days=7)
    with session_scope() as session:
        expired_ids = select(AttemptSyncBatch.id).where(
            AttemptSyncBatch.created_at < cutoff
        ).order_by(AttemptSyncBatch.created_at).limit(max(1, min(limit, 10_000)))
        result = session.execute(
            delete(AttemptSyncBatch).where(AttemptSyncBatch.id.in_(expired_ids))
        )
        return {"deleted": max(0, int(result.rowcount or 0))}


@celery_app.task(name="maintenance.purge_solution_imports")
def purge_solution_imports(limit: int = 100) -> dict[str, int]:
    bounded = max(1, min(limit, 500))
    with session_scope() as session:
        rows = session.scalars(
            select(SolutionImport)
            .where(SolutionImport.expires_at < utcnow())
            .order_by(SolutionImport.expires_at)
            .limit(bounded)
        ).all()
        targets = [(row.id, row.bucket, row.object_key) for row in rows]
    purge_ids: list[str] = []
    for row_id, bucket, object_key in targets:
        if bucket and object_key:
            if storage is None:
                continue
            try:
                storage.remove_object(bucket, object_key)
            except Exception:
                continue
        purge_ids.append(row_id)
    if not purge_ids:
        return {"deleted": 0}
    with session_scope() as session:
        result = session.execute(
            delete(SolutionImport).where(SolutionImport.id.in_(purge_ids))
        )
        return {"deleted": max(0, int(result.rowcount or 0))}


@celery_app.task(name="maintenance.purge_expired_edit_sessions")
def purge_expired_edit_sessions(limit: int = 100) -> dict[str, int]:
    bounded = max(1, min(limit, 500))
    now = utcnow()
    with session_scope() as session:
        rows = session.scalars(
            select(ExamEditSession)
            .where(
                ExamEditSession.status == "active",
                ExamEditSession.expires_at < now,
            )
            .order_by(ExamEditSession.expires_at)
            .limit(bounded)
        ).all()
        targets = [
            job_id
            for row in rows
            for job_id in (row.job_ids or {}).values()
        ]
        for row in rows:
            row.status = "expired"
            row.updated_at = now

    cleaned: list[str] = []
    for job_id in targets:
        if storage is not None:
            try:
                for bucket in {
                    settings.minio_bucket_sources,
                    settings.minio_bucket_assets,
                    settings.minio_bucket_audio,
                }:
                    storage.remove_prefix(bucket, f"jobs/{job_id}/")
            except Exception:
                continue
        cleaned.append(job_id)
    if cleaned:
        with session_scope() as session:
            session.execute(delete(Job).where(Job.id.in_(cleaned)))
    return {"expired": len(rows), "jobs_deleted": len(cleaned)}


@celery_app.task(name="maintenance.purge_full_test_components")
def purge_full_test_components(limit: int = 100) -> dict[str, int]:
    """Abandon stale staging rows, then remove their durable objects boundedly."""

    bounded = max(1, min(limit, 500))
    retention_hours = max(
        1,
        min(int(settings.pending_component_retention_hours), 24 * 7),
    )
    cutoff = utcnow() - timedelta(hours=retention_hours)
    with session_scope() as session:
        stale_ids = abandon_pending_components(
            session,
            updated_before=cutoff,
        )

    with session_scope() as session:
        rows = session.scalars(
            select(Exam)
            .where(Exam.status == "component_abandoned")
            .order_by(Exam.deleted_at)
            .limit(bounded)
        ).all()
        targets = []
        for exam in rows:
            object_refs = {
                (asset.bucket, asset.object_key)
                for asset in session.scalars(
                    select(Asset).where(Asset.exam_id == exam.id)
                )
                if asset.bucket and asset.object_key
            }
            object_refs.update(
                (source.bucket, source.object_key)
                for source in session.scalars(
                    select(ExamSource).where(ExamSource.exam_id == exam.id)
                )
                if source.bucket and source.object_key
            )
            object_refs.update(
                (settings.minio_bucket_answers, answer.source_object_key)
                for answer in session.scalars(
                    select(AnswerKey).where(AnswerKey.exam_id == exam.id)
                )
                if answer.source_object_key
            )
            version_ids = set(
                session.scalars(
                    select(ExamVersion.id).where(ExamVersion.source_exam_id == exam.id)
                )
            )
            targets.append((exam.id, exam.job_id, object_refs, version_ids))

    deleted_ids: list[str] = []
    deleted_job_ids: list[str] = []
    storage_failures = 0
    deleted_version_ids: set[str] = set()
    for exam_id, job_id, object_refs, version_ids in targets:
        try:
            if storage is not None:
                for bucket, object_key in object_refs:
                    if job_id and object_key.startswith(f"jobs/{job_id}/"):
                        continue
                    storage.remove_object(bucket, object_key)
                if job_id:
                    for bucket in {
                        settings.minio_bucket_sources,
                        settings.minio_bucket_assets,
                        settings.minio_bucket_audio,
                    }:
                        storage.remove_prefix(bucket, f"jobs/{job_id}/")
                for version_id in version_ids:
                    for bucket in {
                        settings.minio_bucket_assets,
                        settings.minio_bucket_audio,
                    }:
                        storage.remove_prefix(
                            bucket,
                            f"classroom-versions/{version_id}/",
                        )
        except Exception:
            storage_failures += 1
            continue
        deleted_ids.append(exam_id)
        deleted_version_ids.update(version_ids)

    if deleted_ids:
        candidate_job_ids = {
            job_id
            for exam_id, job_id, _object_refs, _version_ids in targets
            if exam_id in deleted_ids and job_id
        }
        with session_scope() as session:
            session.execute(
                update(Exam)
                .where(Exam.id.in_(deleted_ids))
                .values(current_version_id=None)
            )
            if deleted_version_ids:
                session.execute(
                    delete(ExamVersion).where(
                        ExamVersion.id.in_(deleted_version_ids)
                    )
                )
            session.execute(delete(Exam).where(Exam.id.in_(deleted_ids)))
            session.flush()
            if candidate_job_ids:
                active_sessions = session.scalars(
                    select(ExamEditSession).where(
                        ExamEditSession.status == "active",
                        ExamEditSession.expires_at >= utcnow(),
                    )
                ).all()
                protected_job_ids = {
                    str(job_id)
                    for edit_session in active_sessions
                    for job_id in (edit_session.job_ids or {}).values()
                    if job_id
                }
                referenced_job_ids = set(
                    session.scalars(
                        select(Exam.job_id).where(
                            Exam.job_id.in_(candidate_job_ids)
                        )
                    )
                )
                deleted_job_ids = sorted(
                    candidate_job_ids
                    - protected_job_ids
                    - referenced_job_ids
                )
                if deleted_job_ids:
                    session.execute(
                        delete(Job).where(Job.id.in_(deleted_job_ids))
                    )
    return {
        "stale_abandoned": len(stale_ids),
        "deleted": len(deleted_ids),
        "jobs_deleted": len(deleted_job_ids),
        "storage_failures": storage_failures,
    }


@celery_app.task(name="maintenance.purge_expired_jobs")
def purge_expired_jobs(
    limit: int = 100,
    retention_hours: int = 24,
) -> dict[str, int]:
    """Bound transient OCR data without deleting durable finalized exams.

    ExamSource and immutable version assets are the edit/playback source of
    truth after finalize. Legacy exams that do not yet have an ExamSource keep
    their original Job prefix. Active edit-session jobs are always protected.
    """

    if storage is None:
        return {"deleted": 0, "protected": 0}
    bounded = max(1, min(limit, 500))
    retention = max(6, min(retention_hours, 24 * 30))
    now = utcnow()
    cutoff = now - timedelta(hours=retention)
    with session_scope() as session:
        active_sessions = session.scalars(
            select(ExamEditSession).where(
                ExamEditSession.status == "active",
                ExamEditSession.expires_at >= now,
            )
        ).all()
        active_job_ids = {
            str(job_id)
            for edit_session in active_sessions
            for job_id in (edit_session.job_ids or {}).values()
            if job_id
        }
        candidates = session.scalars(
            select(Job)
            .where(Job.updated_at < cutoff)
            .order_by(Job.updated_at)
            .limit(bounded)
        ).all()
        candidate_ids = {row.id for row in candidates}
        # A pre-migration finalized exam may still depend on jobs/<id>. Keep it
        # until a durable source row exists; current finalize always creates it.
        unsafe_legacy_job_ids = set(
            session.scalars(
                select(Exam.job_id).where(
                    Exam.job_id.in_(candidate_ids),
                    ~Exam.id.in_(select(ExamSource.exam_id)),
                )
            ).all()
        )
        targets = sorted(
            candidate_ids - active_job_ids - unsafe_legacy_job_ids
        )

    cleaned: list[str] = []
    for job_id in targets:
        try:
            for bucket in {
                settings.minio_bucket_sources,
                settings.minio_bucket_assets,
                settings.minio_bucket_audio,
            }:
                storage.remove_prefix(bucket, f"jobs/{job_id}/")
        except Exception:
            continue
        cleaned.append(job_id)
    if cleaned:
        with session_scope() as session:
            session.execute(delete(Job).where(Job.id.in_(cleaned)))
    return {
        "deleted": len(cleaned),
        "protected": len(candidate_ids) - len(targets),
    }
