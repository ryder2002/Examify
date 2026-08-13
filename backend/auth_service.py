"""Cookie authentication and one-time device activation."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from config import settings
from database import session_scope
from identity_cache import identity_cache
from models import Device, RefreshToken, User


ACCESS_COOKIE = "smart_exam_access"
REFRESH_COOKIE = "smart_exam_refresh"
ONBOARDING_COOKIE = "smart_exam_onboarding"
DEFAULT_ACTIVATION_CODE_PREFIX = "EXAMIFY"
password_hasher = PasswordHasher()


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_activation_code(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def create_activation_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    groups = [
        "".join(secrets.choice(alphabet) for _ in range(4))
        for _ in range(4)
    ]
    return f"{DEFAULT_ACTIVATION_CODE_PREFIX}-" + "-".join(groups)


def issue_access_token(user: User, device: Device) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user.id,
            "device_id": device.id,
            "role": user.role,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def issue_onboarding_token(user_id: str, device_id: str, activation_token_id: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "device_id": device_id,
            "activation_token_id": activation_token_id,
            "type": "onboarding",
            "iat": now,
            "exp": now + timedelta(minutes=30),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_onboarding(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=401, detail="Phiên đăng ký đã hết hạn, vui lòng nhập lại Key"
        ) from exc
    if payload.get("type") != "onboarding":
        raise HTTPException(status_code=401, detail="Sai loại phiên đăng ký")
    return payload


def _request_is_secure(request: Request | None) -> bool:
    """Choose cookie security from the external request scheme.

    ``PUBLIC_BASE_URL`` describes the canonical production hostname, but it
    must not force ``Secure`` cookies when an operator accesses a LAN HTTP
    address such as ``http://10.10.10.5``. Nginx supplies the original scheme
    through X-Forwarded-Proto in both HTTP and TLS configurations.
    """

    if request is None:
        return settings.public_base_url.startswith("https://")
    forwarded = request.headers.get("x-forwarded-proto", "")
    scheme = (forwarded.split(",", 1)[0] if forwarded else request.url.scheme).strip().lower()
    return scheme == "https"


def set_onboarding_cookie(
    response: Response, token: str, *, request: Request | None = None
) -> None:
    response.set_cookie(
        ONBOARDING_COOKIE,
        token,
        max_age=30 * 60,
        httponly=True,
        secure=_request_is_secure(request),
        samesite="lax",
        path="/",
    )


def clear_onboarding_cookie(response: Response) -> None:
    response.delete_cookie(ONBOARDING_COOKIE, path="/")


def set_session_cookies(
    response: Response,
    user: User,
    device: Device,
    *,
    request: Request | None = None,
) -> None:
    access, raw_refresh = issue_token_pair(user, device)
    cookie_options = {
        "httponly": True,
        "secure": _request_is_secure(request),
        "samesite": "lax",
        "path": "/",
    }
    set_access_cookie(response, access, request=request)
    response.set_cookie(
        REFRESH_COOKIE,
        raw_refresh,
        max_age=settings.refresh_token_days * 86400,
        **cookie_options,
    )


def issue_token_pair(user: User, device: Device) -> tuple[str, str]:
    """Issue tokens for cookie clients or desktop bearer clients."""
    access = issue_access_token(user, device)
    raw_refresh = secrets.token_urlsafe(48)
    expires = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days)
    with session_scope() as session:
        session.add(
            RefreshToken(
                device_id=device.id,
                token_hash=sha256(raw_refresh),
                expires_at=expires,
            )
        )
    return access, raw_refresh


def set_access_cookie(
    response: Response, access: str, *, request: Request | None = None
) -> None:
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=settings.access_token_minutes * 60,
        httponly=True,
        secure=_request_is_secure(request),
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")
    clear_onboarding_cookie(response)


def decode_access(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập không hợp lệ") from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Sai loại token")
    return payload


def identity_from_access_token(
    token: str,
    *,
    touch_device: bool = True,
    presented_device_key: str | None = None,
) -> dict[str, Any]:
    """Resolve the current durable identity behind an access token.

    The role is intentionally loaded from the database instead of trusted from
    the JWT so WebSocket clients see admin/activation changes immediately.
    """
    payload = decode_access(token)
    device_id = str(payload.get("device_id") or "")
    user_id = str(payload.get("sub") or "")
    cached = identity_cache.get(device_id) if device_id and user_id else None
    if cached and cached.get("user_id") == user_id:
        if (
            cached.get("role") != "admin"
            and cached.get("identity_kind") != "desktop_hardware"
            and (
                not presented_device_key
                or sha256(presented_device_key) != cached.get("device_key_hash")
            )
        ):
            raise HTTPException(
                status_code=401,
                detail="Phiên không thuộc thiết bị đã kích hoạt",
            )
        return {
            key: cached[key]
            for key in (
                "user_id",
                "device_id",
                "role",
                "display_name",
                "email",
                "exam_limit",
                "exam_created_count",
                "registered",
            )
        }
    with session_scope() as session:
        row = session.execute(
            select(Device, User)
            .join(User, User.id == Device.user_id)
            .where(Device.id == device_id, User.id == user_id)
        ).first()
        device, user = row if row is not None else (None, None)
        if (
            device is None
            or user is None
            or device.revoked_at is not None
            or user.status != "active"
        ):
            raise HTTPException(status_code=401, detail="Thiết bị đã bị thu hồi")
        if (
            user.role != "admin"
            and device.identity_kind != "desktop_hardware"
            and (
                not presented_device_key
                or sha256(presented_device_key) != device.device_key_hash
            )
        ):
            raise HTTPException(
                status_code=401,
                detail="Phiên không thuộc thiết bị đã kích hoạt",
            )
        if touch_device:
            now = datetime.now(timezone.utc)
            last_seen = device.last_seen_at
            if last_seen is not None and last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            if (
                last_seen is None
                or now - last_seen
                >= timedelta(
                    seconds=max(10, settings.presence_write_interval_seconds)
                )
            ):
                device.last_seen_at = now
        identity = {
            "user_id": user.id,
            "device_id": device.id,
            "role": user.role,
            "display_name": user.display_name,
            "email": user.email,
            "exam_limit": user.exam_limit,
            "exam_created_count": user.exam_created_count,
            "registered": user.registered_at is not None and bool(user.password_hash),
        }
        identity_cache.put(
            {
                **identity,
                "device_key_hash": device.device_key_hash,
                "identity_kind": device.identity_kind,
            }
        )
        return identity


def current_identity(request: Request, *, required: bool = True) -> dict[str, Any] | None:
    identity = getattr(request.state, "identity", None)
    if identity:
        return identity
    authorization = request.headers.get("authorization", "")
    token = (
        authorization[7:].strip()
        if authorization.lower().startswith("bearer ")
        else request.cookies.get(ACCESS_COOKIE)
    )
    if not token:
        if required:
            raise HTTPException(status_code=401, detail="Thiết bị chưa được kích hoạt")
        return None
    try:
        identity = identity_from_access_token(
            token,
            presented_device_key=request.headers.get("x-examify-device-key"),
        )
    except HTTPException:
        if required:
            raise
        return None
    request.state.identity = identity
    return identity


def identity_from_refresh(request: Request) -> tuple[dict[str, Any], str] | None:
    raw_refresh = request.cookies.get(REFRESH_COOKIE)
    if not raw_refresh:
        return None
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        token = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == sha256(raw_refresh))
        )
        if token is None or token.revoked_at is not None:
            return None
        expires_at = token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            return None
        device = session.get(Device, token.device_id)
        user = session.get(User, device.user_id) if device else None
        if (
            device is None
            or user is None
            or device.revoked_at is not None
            or user.status != "active"
        ):
            return None
        if (
            user.role != "admin"
            and device.identity_kind != "desktop_hardware"
            and (
                not request.headers.get("x-examify-device-key")
                or sha256(request.headers["x-examify-device-key"])
                != device.device_key_hash
            )
        ):
            return None
        device.last_seen_at = now
        identity = {
            "user_id": user.id,
            "device_id": device.id,
            "role": user.role,
            "display_name": user.display_name,
            "email": user.email,
            "exam_limit": user.exam_limit,
            "exam_created_count": user.exam_created_count,
            "registered": user.registered_at is not None and bool(user.password_hash),
        }
        return identity, issue_access_token(user, device)


def require_admin(request: Request) -> dict[str, Any]:
    identity = current_identity(request)
    if identity["role"] != "admin":
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên được phép")
    return identity


def require_roles(request: Request, *roles: str) -> dict[str, Any]:
    identity = current_identity(request)
    if not identity.get("registered"):
        raise HTTPException(status_code=403, detail="Tài khoản chưa hoàn tất đăng ký")
    if identity["role"] not in set(roles):
        raise HTTPException(status_code=403, detail="Bạn không có quyền thực hiện thao tác này")
    return identity


def require_teacher(request: Request) -> dict[str, Any]:
    return require_roles(request, "teacher")


def bootstrap_admin() -> None:
    if not settings.database_url or not settings.admin_password:
        return
    # Multiple Uvicorn workers can run startup concurrently.  Let the
    # database unique constraint elect one creator, then treat the losing
    # insert as a successful bootstrap rather than failing the worker.
    try:
        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == settings.admin_email))
            if user is None:
                # A reset (or a manual seed) may already have installed a
                # valid admin under a different email. Do not recreate the
                # configured bootstrap account alongside it on every restart.
                existing_admin = session.scalar(
                    select(User.id).where(User.role == "admin")
                )
                if existing_admin is not None:
                    return
                session.add(
                    User(
                        email=settings.admin_email,
                        display_name="Administrator",
                        password_hash=password_hasher.hash(settings.admin_password),
                        registered_at=datetime.now(timezone.utc),
                        role="admin",
                    )
                )
    except IntegrityError:
        with session_scope() as session:
            session.scalar(select(User).where(User.email == settings.admin_email))


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def hash_password(password: str) -> str:
    return password_hasher.hash(password)
