"""Maintenance-only reset preserving the selected Admin and terms/privacy.

This command is deliberately never called at application startup. A real reset
requires both a verified backup acknowledgement and an explicit confirmation.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path
from typing import Any


backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config import settings  # noqa: E402
from database import session_scope  # noqa: E402
from models import (  # noqa: E402
    ActivationToken,
    ActivationTokenGroup,
    AnswerKey,
    AntiCheatEvent,
    Asset,
    Attempt,
    AttemptAnswer,
    AttemptSyncBatch,
    AuditLog,
    ClassAssignment,
    ClassMember,
    Classroom,
    ClassroomCoTeacher,
    DesktopSync,
    Device,
    Exam,
    ExamEditSession,
    ExamSource,
    ExamTag,
    ExamVersion,
    ExamVersionAsset,
    ExamVersionQuestion,
    Guide,
    GuideCategory,
    GuideMedia,
    Job,
    PublicExamShare,
    PublicExamSubmission,
    QuestionRecord,
    RefreshToken,
    SitePolicy,
    SolutionImport,
    StimulusRecord,
    SystemState,
    User,
    utcnow,
)
from object_storage import storage  # noqa: E402
from sqlalchemy import delete, func, select, update  # noqa: E402


def _select_admin(session: Any, requested_id: str | None) -> User:
    active_admins = session.scalars(
        select(User).where(User.role == "admin", User.status == "active")
    ).all()
    if requested_id:
        selected = session.get(User, requested_id)
        if selected is None or selected.role != "admin" or selected.status != "active":
            raise RuntimeError("KEEP_ADMIN_ID không trỏ tới một Admin active")
        return selected
    if len(active_admins) != 1:
        raise RuntimeError(
            "Hệ thống phải có đúng một Admin active, hoặc truyền KEEP_ADMIN_ID rõ ràng "
            f"(hiện có {len(active_admins)})."
        )
    return active_admins[0]


def _required_policies(session: Any) -> list[SitePolicy]:
    policies = session.scalars(
        select(SitePolicy).where(SitePolicy.key.in_(["terms", "privacy"]))
    ).all()
    if {policy.key for policy in policies} != {"terms", "privacy"}:
        raise RuntimeError("Reset bị chặn: database phải có đủ policy terms và privacy")
    return policies


DELETE_ORDER: tuple[tuple[str, Any], ...] = (
    ("attempt_answers", AttemptAnswer),
    ("attempt_sync_batches", AttemptSyncBatch),
    ("anti_cheat_events", AntiCheatEvent),
    ("attempts", Attempt),
    ("public_exam_submissions", PublicExamSubmission),
    ("public_exam_shares", PublicExamShare),
    ("class_assignments", ClassAssignment),
    ("exam_edit_sessions", ExamEditSession),
    ("solution_imports", SolutionImport),
    ("exam_sources", ExamSource),
    ("exam_version_assets", ExamVersionAsset),
    ("exam_version_questions", ExamVersionQuestion),
    ("exam_versions", ExamVersion),
    ("answer_keys", AnswerKey),
    ("assets", Asset),
    ("stimuli", StimulusRecord),
    ("questions", QuestionRecord),
    ("desktop_syncs", DesktopSync),
    ("exam_tags", ExamTag),
    ("exams", Exam),
    ("jobs", Job),
    ("classroom_co_teachers", ClassroomCoTeacher),
    ("class_members", ClassMember),
    ("classrooms", Classroom),
    ("guide_media", GuideMedia),
    ("guides", Guide),
    ("guide_categories", GuideCategory),
    ("refresh_tokens", RefreshToken),
    ("devices", Device),
    ("activation_tokens", ActivationToken),
    ("activation_token_groups", ActivationTokenGroup),
    ("audit_logs", AuditLog),
)


def reset_database(*, dry_run: bool, keep_admin_id: str | None) -> dict[str, Any]:
    action = "SẼ XÓA" if dry_run else "ĐÃ XÓA"
    summary: dict[str, Any] = {"tables": {}}
    with session_scope() as session:
        admin = _select_admin(session, keep_admin_id)
        _required_policies(session)
        admin_id = admin.id
        admin_password_hash = admin.password_hash
        admin_email = admin.email
        summary["keep_admin_id"] = admin_id
        summary["keep_admin_email"] = admin_email

        # Break the intentional Exam <-> ExamVersion circular reference before
        # deleting versions. This update is part of the same DB transaction.
        if not dry_run:
            session.execute(update(Exam).values(current_version_id=None))

        for table_name, model in DELETE_ORDER:
            if dry_run:
                count = int(session.scalar(select(func.count()).select_from(model)) or 0)
            else:
                count = max(0, int(session.execute(delete(model)).rowcount or 0))
            summary["tables"][table_name] = count
            print(f"  [DB] {action} {count:>6} dòng: {table_name}")

        if dry_run:
            users_to_delete = int(
                session.scalar(
                    select(func.count(User.id)).where(User.id != admin_id)
                )
                or 0
            )
            policies_to_delete = int(
                session.scalar(
                    select(func.count(SitePolicy.key)).where(
                        ~SitePolicy.key.in_(["terms", "privacy"])
                    )
                )
                or 0
            )
        else:
            users_to_delete = max(
                0,
                int(
                    session.execute(delete(User).where(User.id != admin_id)).rowcount
                    or 0
                ),
            )
            policies_to_delete = max(
                0,
                int(
                    session.execute(
                        delete(SitePolicy).where(
                            ~SitePolicy.key.in_(["terms", "privacy"])
                        )
                    ).rowcount
                    or 0
                ),
            )
            session.execute(
                delete(SystemState).where(SystemState.key != "data_epoch")
            )
            state = session.get(SystemState, "data_epoch")
            if state is None:
                state = SystemState(key="data_epoch", value=str(uuid.uuid4()))
                session.add(state)
            else:
                state.value = str(uuid.uuid4())
                state.updated_at = utcnow()
            session.flush()
            preserved = session.get(User, admin_id)
            if (
                preserved is None
                or preserved.password_hash != admin_password_hash
                or preserved.email != admin_email
            ):
                raise RuntimeError("Verification thất bại: Admin ID/email/password hash đã đổi")
            if set(session.scalars(select(SitePolicy.key)).all()) != {"terms", "privacy"}:
                raise RuntimeError("Verification thất bại: policy sau reset không chính xác")
            if set(session.scalars(select(SystemState.key)).all()) != {"data_epoch"}:
                raise RuntimeError("Verification thất bại: SystemState còn dữ liệu epoch cũ")
            summary["data_epoch"] = state.value

        other_state_count = int(
            session.scalar(
                select(func.count(SystemState.key)).where(
                    SystemState.key != "data_epoch"
                )
            )
            or 0
        ) if dry_run else 0
        summary["tables"]["system_state_except_data_epoch"] = other_state_count

        summary["users_to_delete"] = users_to_delete
        summary["policies_to_delete"] = policies_to_delete
        print(f"  [DB] {action} {users_to_delete:>6} user, giữ Admin id={admin_id}")
        print(f"  [DB] {action} {policies_to_delete:>6} policy phụ, giữ terms/privacy")
    return summary


def reset_minio(*, dry_run: bool) -> dict[str, int]:
    if storage is None or not hasattr(storage, "client"):
        raise RuntimeError("MinIO chưa được cấu hình; không cho phép reset một phần")
    buckets = sorted(
        {
            settings.minio_bucket_sources,
            settings.minio_bucket_assets,
            settings.minio_bucket_audio,
            settings.minio_bucket_answers,
            settings.minio_bucket_guides,
        }
    )
    summary: dict[str, int] = {}
    for bucket in buckets:
        if not storage.client.bucket_exists(bucket):
            summary[bucket] = 0
            print(f"  [MinIO] Bucket chưa tồn tại: {bucket}")
            continue
        objects = list(storage.client.list_objects(bucket, recursive=True))
        summary[bucket] = len(objects)
        if not dry_run:
            for item in objects:
                storage.client.remove_object(bucket, item.object_name)
        print(
            f"  [MinIO] {'SẼ XÓA' if dry_run else 'ĐÃ XÓA'} "
            f"{len(objects):>6} object: {bucket}"
        )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset dữ liệu nghiệp vụ có kiểm soát")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ kiểm tra Admin/policy và in số dòng/object, không thay đổi dữ liệu",
    )
    parser.add_argument(
        "--keep-admin-id",
        default=os.getenv("KEEP_ADMIN_ID") or None,
        help="Bắt buộc khi có nhiều hơn một Admin active",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.dry_run:
        if os.getenv("BACKUP_VERIFIED") != "YES":
            raise SystemExit("TỪ CHỐI RESET: cần BACKUP_VERIFIED=YES sau khi restore verification")
        if os.getenv("CONFIRM_RESET") != "YES":
            raise SystemExit("TỪ CHỐI RESET: cần CONFIRM_RESET=YES")
    print("=== RESET DRY-RUN ===" if args.dry_run else "=== RESET MAINTENANCE ===")
    database_summary = reset_database(
        dry_run=args.dry_run, keep_admin_id=args.keep_admin_id
    )
    minio_summary = reset_minio(dry_run=args.dry_run)
    print(
        "Hoàn tất dry-run; chưa có dữ liệu bị thay đổi."
        if args.dry_run
        else (
            "Reset DB + MinIO hoàn tất; Admin ID/hash và terms/privacy được giữ, "
            f"data_epoch mới={database_summary['data_epoch']}."
        )
    )
    print(f"MinIO objects: {sum(minio_summary.values())}")


if __name__ == "__main__":
    main()
