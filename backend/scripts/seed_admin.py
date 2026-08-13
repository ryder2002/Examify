"""Create or update the configured administrator account idempotently."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from auth_service import hash_password
from config import settings
from database import session_scope
from models import User


def main() -> None:
    email = settings.admin_email.strip().lower()
    if not email or not settings.admin_password:
        raise SystemExit("ADMIN_EMAIL and ADMIN_PASSWORD are required")

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, display_name="Administrator")
            session.add(user)
            action = "created"
        else:
            action = "updated"

        user.display_name = "Administrator"
        user.password_hash = hash_password(settings.admin_password)
        user.registered_at = user.registered_at or now
        user.role = "admin"
        user.status = "active"

    print(f"admin seed {action}: {email}")


if __name__ == "__main__":
    main()
