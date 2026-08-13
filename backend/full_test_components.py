"""Lifecycle helpers for unpublished Full Test components."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Exam, User, utcnow


logger = logging.getLogger(__name__)


def abandon_pending_components(
    session: Session,
    *,
    owner_user_id: str | None = None,
    exam_ids: set[str] | None = None,
    updated_before: datetime | None = None,
) -> list[str]:
    """Atomically hide pending components and refund any reserved quota.

    At least one scope is mandatory so a caller cannot accidentally abandon
    every active Full Test in the database.
    """

    if owner_user_id is None and not exam_ids and updated_before is None:
        raise ValueError("Pending component cleanup requires a bounded scope")

    query = select(Exam).where(
        Exam.status == "component_pending",
        Exam.deleted_at.is_(None),
    )
    if owner_user_id is not None:
        query = query.where(Exam.owner_user_id == owner_user_id)
    if exam_ids:
        query = query.where(Exam.id.in_(exam_ids))
    if updated_before is not None:
        query = query.where(Exam.updated_at < updated_before)

    rows = session.scalars(query.order_by(Exam.updated_at).with_for_update()).all()
    if not rows:
        return []

    now = utcnow()
    reading_refunds: dict[str, int] = {}
    for exam in rows:
        exam.status = "component_abandoned"
        exam.deleted_at = now
        exam.shared_title_key = None
        exam.updated_at = now
        if exam.owner_user_id and exam.exam_type == "reading":
            reading_refunds[exam.owner_user_id] = (
                reading_refunds.get(exam.owner_user_id, 0) + 1
            )

    for user_id, refund in reading_refunds.items():
        user = session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        # Reading staging reserves one quota slot; Listening staging does not.
        # Admins and unlimited accounts never reserve quota.
        if user and user.role != "admin" and user.exam_limit is not None:
            user.exam_created_count = max(0, int(user.exam_created_count or 0) - refund)

    ids = [exam.id for exam in rows]
    logger.info(
        "FULL_TEST_COMPONENTS_ABANDONED owner=%s count=%s ids=%s",
        owner_user_id or "stale",
        len(ids),
        ",".join(ids),
    )
    return ids
