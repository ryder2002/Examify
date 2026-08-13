"""SQLAlchemy session and migration bootstrap."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import settings


class Base(DeclarativeBase):
    pass


def _engine_options() -> dict:
    options: dict = {
        "pool_pre_ping": True,
        "pool_size": max(1, settings.database_pool_size),
        "max_overflow": max(0, settings.database_max_overflow),
        "pool_timeout": max(1, settings.database_pool_timeout_seconds),
        "pool_recycle": max(60, settings.database_pool_recycle_seconds),
    }
    if make_url(settings.database_url).get_backend_name() == "postgresql":
        options["connect_args"] = {
            "connect_timeout": max(1, settings.database_connect_timeout_seconds),
            # Transaction pooling cannot safely reuse named server prepared
            # statements across backend connections.
            "prepare_threshold": None,
            "options": " ".join(
                [
                    f"-c statement_timeout={max(1000, settings.database_statement_timeout_ms)}",
                    f"-c lock_timeout={max(100, settings.database_lock_timeout_ms)}",
                    "-c idle_in_transaction_session_timeout="
                    f"{max(1000, settings.database_idle_transaction_timeout_ms)}",
                ]
            ),
        }
    return options


engine = (
    create_engine(settings.database_url, **_engine_options())
    if settings.database_url
    else None
)
SessionLocal = (
    sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    if engine is not None
    else None
)


@contextmanager
def session_scope() -> Iterator[Session]:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL chưa được cấu hình")
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_schema() -> None:
    if engine is None:
        return
    # Models register their tables on Base.metadata.
    import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
