"""Access and naming rules for the teacher-owned shared exam bank."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from exam_solutions import normalized_name_key
from models import ClassMember, Classroom, Exam


def teacher_scoped_title_key(owner_user_id: str, title: str) -> str:
    """Normalize a bank title within its teacher owner's namespace."""
    name_key = normalized_name_key(title)
    if not owner_user_id or not name_key:
        return ""
    return f"{owner_user_id}:{name_key}"


def exam_bank_visibility_filters(identity: dict[str, Any]) -> list[Any]:
    """Return SQL filters for exams visible to one authenticated identity.

    A student receives a teacher's bank when they have an active membership in
    *any* classroom owned by that teacher. This deliberately does not consult
    manual class-publication rows: those rows govern assignments, while the
    bank is a teacher-owned catalogue.
    """
    role = identity["role"]
    if role == "admin":
        return []
    if role == "teacher":
        return [Exam.owner_user_id == identity["user_id"]]
    if role == "student":
        teacher_membership = (
            select(ClassMember.id)
            .join(Classroom, Classroom.id == ClassMember.classroom_id)
            .where(
                ClassMember.user_id == identity["user_id"],
                ClassMember.status == "active",
                Classroom.owner_teacher_id == Exam.owner_user_id,
            )
            .correlate(Exam)
            .exists()
        )
        return [teacher_membership]
    # The caller validates roles before use, but a deny-all predicate avoids a
    # future role accidentally inheriting the entire shared bank.
    return [Exam.id.is_(None)]
