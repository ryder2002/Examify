"""FastAPI application for asynchronous TOEIC PDF extraction."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import logging
import os
import random
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal
import httpx
from pydantic import BaseModel, Field

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from answer_key import answer_key_scope_detail, extract_answer_key_image
from audio_processing import prepare_web_audio
from exam_solutions import SolutionValidationError, validate_solutions
from exam_bank_scope import teacher_scoped_title_key
from job_store import store
from pipeline import (
    create_manual_stimulus,
    extract_exam,
    ocr_dependency_status,
    recrop_asset,
)
from schemas import (
    AudioRef,
    DraftPatch,
    ExamDraft,
    FinalExam,
    FinalizeRequest,
    ManualStimulusRequest,
    Question,
    Stimulus,
    ensure_question_coverage,
)
from config import settings
from metrics import (
    HTTP_DURATION,
    HTTP_IN_FLIGHT,
    HTTP_REQUESTS,
    observe_pool,
    render_metrics,
    route_template,
)

if not settings.desktop:
    from database import engine, session_scope
    from object_storage import storage
    from auth_service import (
        bootstrap_admin,
        current_identity,
        identity_from_access_token,
        identity_from_refresh,
        ACCESS_COOKIE,
        set_access_cookie,
    )
    from platform_api import persist_final_exam, router as platform_router
    from desktop_sync_api import router as desktop_sync_router
    from dictionary_api import router as dictionary_router
    from guide_api import router as guide_router
    from classroom_api import router as classroom_router
    from exam_bank_api import router as exam_bank_router, v2_router as exam_bank_v2_router
    from rate_limit import RateLimitExceeded, rate_limiter
else:
    storage = None

    def current_identity(_request: Request, *, required: bool = True):
        return None


def _desktop_user_id(request: Request) -> str:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    raw_user_id = (
        request.headers.get("x-toeicdoc-user-id", "")
        or request.query_params.get("desktop_user", "")
    ).strip()
    try:
        user_id = str(uuid.UUID(raw_user_id))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Desktop chưa ràng buộc dữ liệu với tài khoản đăng nhập",
        ) from exc
    return user_id


def _desktop_store(request: Request):
    """Return the isolated local store for the active desktop account."""

    user_id = _desktop_user_id(request)
    from desktop_store import DesktopStore

    # Keep the old root database untouched: it has no trustworthy owner id and
    # must never be assigned to whichever account happens to log in first.
    return DesktopStore(Path(settings.desktop_data_dir) / "users" / user_id)


class JsonLogFormatter(logging.Formatter):
    """Emit one bounded JSON object per line for production log collectors."""

    _structured_fields = (
        "request_id",
        "method",
        "route",
        "status",
        "duration_ms",
        "user_id",
        "attempt_id",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for field in self._structured_fields:
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tool-tao-de")
logger.handlers.clear()
json_log_handler = logging.StreamHandler()
json_log_handler.setFormatter(JsonLogFormatter())
logger.addHandler(json_log_handler)
logger.setLevel(logging.INFO)
logger.propagate = False

ALLOWED_EXT = {".pdf"}
ALLOWED_AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".webm": "audio/webm",
    ".flac": "audio/flac",
}
MAX_BYTES = 50 * 1024 * 1024
MAX_AUDIO_BYTES = 50 * 1024 * 1024
MAX_TOTAL_AUDIO_BYTES = 300 * 1024 * 1024
MAX_ANSWER_IMAGE_BYTES = 10 * 1024 * 1024
EXTRACTION_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="toeic-ocr")


def _uses_persistent_job_objects() -> bool:
    return bool(
        not settings.desktop
        and settings.persistence_enabled
        and storage is not None
    )


def _persistent_job_object_response(
    request: Request,
    *,
    bucket: str,
    object_key: str,
    content_type: str,
) -> Response:
    """Authorize in FastAPI, then keep immutable media bytes out of Python."""

    if storage is None:
        raise HTTPException(status_code=503, detail="Object storage chưa sẵn sàng")
    safe_key = storage.safe_key(object_key)
    headers = {
        "Cache-Control": "private, max-age=3600",
        "Accept-Ranges": "bytes",
    }
    if settings.minio_accel_redirect_prefix:
        headers.update(
            {
                "X-Accel-Redirect": storage.presigned_internal_redirect(
                    bucket,
                    safe_key,
                    settings.minio_accel_redirect_prefix,
                    method=request.method,
                ),
                "X-Accel-Expires": "3600",
            }
        )
        return Response(media_type=content_type, headers=headers)

    # Desktop does not enter this branch. This fallback keeps development
    # deployments correct when Nginx internal redirects are unavailable,
    # without materializing a durable cache file in API scratch space.
    response = storage.client.get_object(bucket, safe_key)

    def body():
        try:
            yield from response.stream(1024 * 1024)
        finally:
            response.close()
            response.release_conn()

    return StreamingResponse(body(), media_type=content_type, headers=headers)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not settings.desktop:
        try:
            bootstrap_admin()
        except Exception:
            # Readiness reports the dependency failure and keeps the process
            # alive so a transient PgBouncer/PostgreSQL outage does not turn
            # into a Uvicorn crash loop or an Nginx 502 storm. Existing admin
            # data is unaffected; the bootstrap is retried on the next deploy.
            logging.getLogger(__name__).exception(
                "STARTUP_ADMIN_BOOTSTRAP_DEFERRED"
            )
    store.cleanup()
    yield


app = FastAPI(title="Tool Tạo Đề TOEIC", version="2.0.0", lifespan=lifespan)
if not settings.desktop:
    app.include_router(platform_router)
    app.include_router(desktop_sync_router)
    app.include_router(dictionary_router)
    app.include_router(guide_router)
    app.include_router(classroom_router)
    app.include_router(exam_bank_router)
    app.include_router(exam_bank_v2_router)
server_origins = {
    "http://localhost",
    "http://localhost:3000",
    "http://127.0.0.1",
    "http://127.0.0.1:3000",
    "http://exam.congnhat.online",
    "https://exam.congnhat.online",
    "http://www.exam.congnhat.online",
    "https://www.exam.congnhat.online",
    "http://tauri.localhost",
    "tauri://localhost",
    "https://tauri.localhost",
}
if settings.public_base_url:
    server_origins.add(settings.public_base_url.rstrip("/"))
server_origins.update(
    origin.strip().rstrip("/")
    for origin in settings.cors_allowed_origins.split(",")
    if origin.strip()
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(server_origins),
    # Keep private LAN IP access safe when frontend and API use different
    # ports. Same-origin access through nginx does not need this fallback.
    allow_origin_regex=(
        r"^https?://(?:(?:localhost|127\.0\.0\.1)(?::\d+)?|"
        r"10(?:\.\d{1,3}){3}(?::\d+)?|"
        r"192\.168(?:\.\d{1,3}){2}(?::\d+)?|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}(?::\d+)?)$"
    ),
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Authorization",
        "Content-Type",
        "If-Range",
        "Idempotency-Key",
        "Range",
        "X-Classroom-Session",
        "X-Desktop-Secret",
        "X-Examify-Device-Key",
        "X-Examify-Desktop-Version",
        "X-TOEICDOC-User-ID",
        "X-Request-ID",
    ],
)

@app.middleware("http")
async def device_authentication(request: Request, call_next):
    path = request.url.path
    # CORS preflight never carries the per-session header.  It must reach
    # CORSMiddleware before desktop endpoints validate the real request.
    if request.method == "OPTIONS":
        return await call_next(request)
    if settings.desktop and path.startswith("/api/"):
        supplied = request.headers.get("x-desktop-secret", "")
        # Browser media tags cannot attach a custom header.  Asset URLs carry
        # the per-launch secret as a loopback-only query parameter instead.
        if not supplied:
            supplied = request.query_params.get("desktop_secret", "")
        if not settings.desktop_secret or supplied != settings.desktop_secret:
            return JSONResponse(
                status_code=401,
                content={"detail": "Desktop session không hợp lệ"},
            )
        return await call_next(request)
    if path.startswith("/api/v1/desktop/") and settings.min_desktop_auth_version:
        supplied_version = request.headers.get("x-examify-desktop-version", "").strip()

        def version_tuple(value: str) -> tuple[int, ...]:
            try:
                return tuple(int(part) for part in value.split("-", 1)[0].split("."))
            except ValueError:
                return ()

        if (
            not supplied_version
            or version_tuple(supplied_version)
            < version_tuple(settings.min_desktop_auth_version)
        ):
            return JSONResponse(
                status_code=426,
                content={
                    "detail": "Phiên bản Examify Desktop đã cũ; vui lòng nâng cấp trước khi tiếp tục",
                    "minimum_version": settings.min_desktop_auth_version,
                },
            )
    public_paths = {
        "/health",
        "/health/live",
        "/health/ready",
        "/api/v1/activations/redeem",
        "/api/v1/auth/login",
        "/api/v1/auth/device-status",
        "/api/v1/auth/register",
        "/api/v1/auth/state",
        "/api/v1/auth/refresh",
        "/api/v1/desktop/activate",
        "/api/v1/desktop/auth/register",
        "/api/v1/desktop/auth/login",
        "/api/v1/desktop/auth/logout",
        "/api/v1/desktop/auth/refresh",
        "/docs",
        "/openapi.json",
    }
    public_policy_paths = {"/api/v1/policies/terms", "/api/v1/policies/privacy"}
    if (
        settings.auth_required
        and path.startswith("/api/")
        and path not in public_paths | public_policy_paths
        and not path.startswith("/api/v1/class-session/")
        and not path.startswith("/api/v1/class-assets/")
        and not path.startswith("/api/v1/public-tests/")
        and "/assets/" not in path
    ):
        refreshed_access: str | None = None
        try:
            identity = current_identity(request)
        except HTTPException as exc:
            refreshed = identity_from_refresh(request)
            if refreshed is None:
                return JSONResponse(
                    status_code=exc.status_code, content={"detail": exc.detail}
                )
            identity, refreshed_access = refreshed
            request.state.identity = identity
        if not identity.get("registered"):
            return JSONResponse(
                status_code=403,
                content={"detail": "Tài khoản chưa hoàn tất đăng ký"},
            )
        role = identity["role"]
        creator_paths = (
            path.startswith("/api/extractions")
            or path.startswith("/api/v1/exams")
            or path.startswith("/api/v1/tags")
            or path.startswith("/api/v1/desktop/sync")
        )
        if creator_paths and role not in {"teacher", "user", "admin"}:
            return JSONResponse(
                status_code=403,
                content={"detail": "Vai trò này không được sử dụng chức năng tạo đề"},
            )
        if path.startswith("/api/v1/teacher/") and role != "teacher":
            return JSONResponse(status_code=403, content={"detail": "Chỉ giáo viên được phép"})
        if path.startswith("/api/v1/student/") and role != "student":
            return JSONResponse(status_code=403, content={"detail": "Chỉ học viên được phép"})
        if path.startswith("/api/v1/admin/") and role != "admin":
            return JSONResponse(status_code=403, content={"detail": "Chỉ quản trị viên được phép"})
        guide_paths = (
            path == "/api/v1/guides"
            or path == "/api/v1/guides/search"
            or path == "/api/v1/guide-categories"
            or path.startswith("/api/v1/guides/")
            or path.startswith("/api/v1/guide-media/")
        )
        if guide_paths and role != "admin":
            return JSONResponse(status_code=403, content={"detail": "Chỉ quản trị viên được phép"})
        try:
            decision = await asyncio.to_thread(rate_limiter.check, request, identity)
            request.state.rate_limit_decision = decision
        except RateLimitExceeded as exc:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Quá nhiều yêu cầu, vui lòng thử lại sau",
                    "policy": exc.policy.name,
                },
                headers={
                    "Retry-After": str(exc.retry_after),
                    "X-RateLimit-Limit": str(exc.policy.user_limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
        response = await call_next(request)
        if refreshed_access:
            set_access_cookie(response, refreshed_access, request=request)
        decision = getattr(request.state, "rate_limit_decision", None)
        if decision is not None and not decision.exempt:
            response.headers["X-RateLimit-Limit"] = str(decision.limit)
            response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response
    if not settings.desktop and path.startswith("/api/"):
        # Public endpoints can still be called by an authenticated Teacher.
        # Resolve the durable role before applying the limiter so it receives
        # the separate higher-budget lane without bypassing upload/CPU limits.
        identity = getattr(request.state, "identity", None)
        if identity is None:
            identity = current_identity(request, required=False)
        if path.endswith("/auth/login"):
            try:
                login_payload = json.loads((await request.body()).decode("utf-8"))
                subject = str(login_payload.get("email") or "").strip().casefold()
                if subject:
                    request.state.rate_limit_subject = subject[:320]
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                pass
        try:
            decision = await asyncio.to_thread(rate_limiter.check, request, identity)
            request.state.rate_limit_decision = decision
        except RateLimitExceeded as exc:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Quá nhiều yêu cầu, vui lòng thử lại sau",
                    "policy": exc.policy.name,
                },
                headers={
                    "Retry-After": str(exc.retry_after),
                    "X-RateLimit-Limit": str(exc.policy.ip_limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
        response = await call_next(request)
        if decision is not None and not decision.exempt:
            response.headers["X-RateLimit-Limit"] = str(decision.limit)
            response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response
    return await call_next(request)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    supplied_request_id = request.headers.get("x-request-id", "")
    request_id = (
        supplied_request_id
        if supplied_request_id
        and len(supplied_request_id) <= 64
        and all(character.isalnum() or character in "-_" for character in supplied_request_id)
        else uuid.uuid4().hex
    )
    request.state.request_id = request_id
    started_at = time.perf_counter()
    status_code = 500
    HTTP_IN_FLIGHT.inc()
    try:
        response = await call_next(request)
        status_code = response.status_code
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
        return response
    finally:
        elapsed_seconds = time.perf_counter() - started_at
        route = route_template(request)
        HTTP_IN_FLIGHT.dec()
        HTTP_REQUESTS.labels(request.method, route, str(status_code)).inc()
        HTTP_DURATION.labels(request.method, route).observe(elapsed_seconds)
        if not settings.desktop:
            observe_pool(engine)
        quiet_success = status_code < 400 and (
            request.url.path in {"/health", "/health/live", "/health/ready", "/internal/metrics"}
            or request.url.path.endswith("/heartbeat")
            or "/assets/" in request.url.path
        )
        if not quiet_success:
            identity = getattr(request.state, "identity", None) or {}
            user_id = str(identity.get("user_id") or "")[:12]
            attempt_id = str(request.path_params.get("attempt_id") or "")[:12]
            logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "route": route,
                    "status": status_code,
                    "duration_ms": round(elapsed_seconds * 1000, 1),
                    "user_id": user_id,
                    "attempt_id": attempt_id,
                },
            )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "2.0.0"}


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
def health_ready():
    dependencies = ocr_dependency_status()
    checks: dict[str, bool] = {}
    scratch: dict[str, int] | None = None
    try:
        usage = shutil.disk_usage(store.root)
        free_percent = int((usage.free * 100) / max(1, usage.total))
        scratch = {
            "total_bytes": usage.total,
            "free_bytes": usage.free,
            "free_percent": free_percent,
        }
        checks["scratch"] = bool(
            usage.free >= settings.scratch_min_free_bytes
            and free_percent >= settings.scratch_min_free_percent
        )
    except OSError:
        logger.warning("READINESS_SCRATCH_FAILED")
        checks["scratch"] = False
    if settings.persistence_enabled:
        try:
            from sqlalchemy import text

            with session_scope() as session:
                session.execute(text("SELECT 1"))
            checks["postgres"] = True
        except Exception:
            logger.warning("READINESS_POSTGRES_FAILED")
            checks["postgres"] = False
        try:
            checks["minio"] = bool(
                storage
                and storage.client.bucket_exists(settings.minio_bucket_sources)
            )
        except Exception:
            logger.warning("READINESS_MINIO_FAILED")
            checks["minio"] = False
    if settings.use_celery:
        redis_client = None
        try:
            from redis import Redis

            redis_client = Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            checks["redis"] = bool(redis_client.ping())
        except Exception:
            logger.warning("READINESS_REDIS_FAILED")
            checks["redis"] = False
        finally:
            if redis_client is not None:
                redis_client.close()
    ready = bool(dependencies.get("ocr_ready")) and (
        all(checks.values()) if checks else True
    )
    payload = {
        "status": "ready" if ready else "not_ready",
        "persistence": settings.persistence_enabled,
        "queue": settings.use_celery,
        "checks": checks,
        "profile": settings.app_profile,
        "processing_location": "LOCAL_EDGE" if settings.desktop else "REMOTE_SERVER",
        "edge_ocr": settings.desktop,
        "ocr_enabled": settings.ocr_enabled,
        "ocr_engine": "tesseract",
        "scratch": scratch,
        **dependencies,
    }
    if not settings.desktop and engine is not None:
        pool = engine.pool
        payload["database_pool"] = {
            "size": getattr(pool, "size", lambda: 0)(),
            "checked_in": getattr(pool, "checkedin", lambda: 0)(),
            "checked_out": getattr(pool, "checkedout", lambda: 0)(),
            "overflow": max(0, getattr(pool, "overflow", lambda: 0)()),
        }
    return JSONResponse(payload, status_code=200 if ready else 503)


@app.get("/internal/metrics", include_in_schema=False)
def internal_metrics() -> Response:
    """Scraped only over the internal Docker network; nginx does not expose it."""

    if not settings.desktop:
        observe_pool(engine)
    payload, content_type = render_metrics()
    return Response(content=payload, headers={"Content-Type": content_type})


@app.websocket("/api/v1/ws/identity")
async def identity_websocket(websocket: WebSocket) -> None:
    """Push durable role/account changes to both browser and Tauri clients."""
    if settings.desktop:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    token = websocket.cookies.get(ACCESS_COOKIE)
    try:
        message = await asyncio.wait_for(websocket.receive_json(), timeout=8)
        if not token:
            token = str(message.get("access_token") or "")
        device_key = str(message.get("device_key") or "")
        if not token:
            raise HTTPException(status_code=401, detail="Thiếu phiên đăng nhập")
        last_identity: tuple[str, str, str] | None = None
        heartbeat_ticks = 0
        while True:
            identity = await asyncio.to_thread(
                identity_from_access_token,
                token,
                touch_device=False,
                presented_device_key=device_key or None,
            )
            current = (
                str(identity["user_id"]),
                str(identity["role"]),
                str(identity["display_name"]),
            )
            if current != last_identity:
                await websocket.send_json(
                    {
                        "type": "identity",
                        "user_id": current[0],
                        "role": current[1],
                        "display_name": current[2],
                    }
                )
                last_identity = current
            elif heartbeat_ticks >= 4:
                await websocket.send_json({"type": "ping"})
                heartbeat_ticks = 0
            heartbeat_ticks += 1
            await asyncio.sleep(
                max(5, settings.identity_websocket_interval_seconds)
            )
    except HTTPException as exc:
        await websocket.send_json({"type": "error", "detail": exc.detail})
        await websocket.close(code=4401)
    except asyncio.TimeoutError:
        await websocket.close(code=4401)
    except (WebSocketDisconnect, RuntimeError):
        return


def _check_job_access(job_id: str, request: Request, write: bool = False) -> None:
    if settings.desktop:
        owner_id = store.owner_id(job_id) if hasattr(store, "owner_id") else None
        if not owner_id or owner_id != _desktop_user_id(request):
            raise HTTPException(status_code=404, detail="Không tìm thấy job")
        return
    if not settings.auth_required:
        return
    identity = current_identity(request, required=False)
    if identity is None:
        raise HTTPException(status_code=401, detail="Chưa đăng nhập")
    if write:
        owner_id = store.owner_id(job_id) if hasattr(store, "owner_id") else None
        if owner_id and owner_id != identity["user_id"] and identity["role"] != "admin":
            raise HTTPException(status_code=403, detail="Không có quyền chỉnh sửa job này")


def _state_question_range(state: dict[str, object]) -> tuple[int, int] | None:
    """Read the bounded question span detected for a full or partial upload."""
    raw = (state.get("metadata") or {}).get("detected_question_range")
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        start, end = int(raw[0]), int(raw[1])
    except (TypeError, ValueError):
        return None
    exam_type = str(state.get("exam_type") or "")
    lower, upper = (1, 100) if exam_type == "listening" else (101, 200)
    if lower <= start <= end <= upper:
        return start, end
    return None


def _run_extraction(
    job_id: str,
    input_path: Path,
    *,
    overall_progress_start: int = 0,
    progress_callback: Callable[[int, str], None] | None = None,
    before_finalize: Callable[[], None] | None = None,
    failure_callback: Callable[[Exception], None] | None = None,
) -> None:
    try:
        state = store.read(job_id)
        location = "LOCAL_EDGE" if settings.desktop else "REMOTE_SERVER"
        started_at = time.perf_counter()
        logger.info(
            "[OCR_ROUTE] location=%s event=processing_started job=%s type=%s pid=%s",
            location,
            job_id,
            state.get("exam_type"),
            os.getpid(),
        )

        def report(progress: int, stage: str) -> None:
            try:
                phase_progress = max(0, min(100, int(progress)))
                if progress_callback is not None:
                    progress_callback(phase_progress, stage)
                    return
                overall_progress = overall_progress_start + round(
                    phase_progress * (100 - overall_progress_start) / 100
                )
                changes = {
                    "status": "processing",
                    "processing_phase": "ocr",
                    "phase_progress": phase_progress,
                    "ocr_progress": phase_progress,
                    "ocr_stage": stage,
                    "progress": overall_progress,
                    "stage": stage,
                }
                if hasattr(store, "write_progress"):
                    store.write_progress(job_id, **changes)
                else:
                    current = store.read(job_id)
                    current.update(changes)
                    store.write(job_id, current)
            except Exception as report_exc:
                logger.warning("Failed to update progress report for job %s: %s", job_id, report_exc)

        report(1, "Bắt đầu xử lý")
        result = extract_exam(
            job_id=job_id,
            pdf_path=str(input_path),
            exam_type=state["exam_type"],
            job_dir=store.job_dir(job_id),
            progress=report,
        )
        extracted_questions = [
            Question.model_validate(item) for item in result["questions"]
        ]
        detected_range = result.get("metadata", {}).get("detected_question_range")
        question_range = None
        if isinstance(detected_range, (list, tuple)) and len(detected_range) == 2:
            try:
                question_range = (int(detected_range[0]), int(detected_range[1]))
            except (TypeError, ValueError):
                question_range = None
        extracted_questions, inserted_numbers = ensure_question_coverage(
            state["exam_type"], extracted_questions, question_range
        )
        if inserted_numbers:
            logger.warning(
                "[OCR_MANUAL_QUESTION] job=%s inserted_missing_numbers=%s",
                job_id,
                inserted_numbers,
            )
        if before_finalize is not None:
            before_finalize()
        final_state = store.read(job_id)
        metadata = final_state.get("metadata", {})
        metadata.update(result["metadata"])
        result_issues = list(result["issues"])
        audio_autocut = metadata.get("audio_autocut") or {}
        if audio_autocut.get("status") == "fallback":
            result_issues.append(
                {
                    "code": "audio_autocut_fallback",
                    "message": (
                        "Không thể xác định đủ chắc chắn 54 mốc audio TOEIC; "
                        "hệ thống giữ Audio Full để tránh gán sai câu."
                    ),
                    "severity": "warning",
                }
            )
        final_state.update(
            {
                "status": "review",
                "processing_phase": "review",
                "phase_progress": 100,
                "stage": "Sẵn sàng kiểm tra",
                "progress": 100,
                "ocr_progress": 100,
                "ocr_stage": "Đã hoàn tất OCR",
                "questions": [
                    question.model_dump() for question in extracted_questions
                ],
                "stimuli": result["stimuli"],
                "issues": result_issues,
                "returned_count": len(extracted_questions),
                "metadata": metadata,
            }
        )
        if final_state.get("audios") or final_state.get("audio"):
            final_state["audio_progress"] = 100
            final_state["audio_stage"] = "Đã xử lý xong audio"
        store.write(job_id, final_state)
        logger.info(
            "[OCR_ROUTE] location=%s event=processing_completed job=%s questions=%s duration=%.2fs",
            location,
            job_id,
            len(result["questions"]),
            time.perf_counter() - started_at,
        )
    except Exception as exc:
        if failure_callback is not None:
            try:
                # Signal sibling branches before publishing the terminal
                # state, so a late audio progress callback cannot resurrect a
                # failed OCR job as ``processing``.
                failure_callback(exc)
            except Exception:
                logger.warning(
                    "Failed to notify extraction failure for job %s",
                    job_id,
                    exc_info=True,
                )
        logger.exception("[OCR_ROUTE] Extraction job %s failed: %s", job_id, exc)
        try:
            failed = store.read(job_id)
        except Exception:
            failed = {"job_id": job_id, "status": "failed"}
        failed.update(
            {
                "status": "failed",
                "stage": "Xử lý thất bại",
                "error": str(exc) or "Lỗi không xác định trong quá trình đọc PDF / OCR",
            }
        )
        try:
            store.write(job_id, failed)
        except Exception as write_exc:
            logger.error("Could not write failed state for job %s: %s", job_id, write_exc)


def _run_extraction_job(job_id: str, input_path: Path) -> None:
    """Run bounded audio/OCR orchestration in Celery and Desktop."""
    try:
        state = store.read(job_id)
        has_audio = bool(state.get("audios") or state.get("audio"))
        # Audio preparation is independent from PDF/OCR work.  Keep it on a
        # bounded sibling thread for both server-side remote OCR and the
        # desktop/local OCR path.  Previously this was gated by
        # the remote OCR setting so Desktop jobs always waited for FFmpeg before
        # OCR started, which made the progress bar appear stuck at 1%.
        parallel_audio = has_audio
        last_audio_progress = -10

        def report_audio(phase_progress: int, stage: str) -> None:
            nonlocal last_audio_progress
            phase_progress = max(0, min(100, int(phase_progress)))
            # Persist bounded milestones, not per-frame FFmpeg output. This
            # keeps polling responsive without creating a PostgreSQL/MinIO
            # write storm while dozens of clips are encoded.
            if phase_progress < 100 and phase_progress - last_audio_progress < 3:
                return
            last_audio_progress = phase_progress
            changes = {
                "status": "processing",
                "processing_phase": "audio",
                "phase_progress": phase_progress,
                "audio_progress": phase_progress,
                "audio_stage": stage,
                "stage": stage,
                "progress": 1 + round(phase_progress * 19 / 100),
            }
            if hasattr(store, "write_progress"):
                store.write_progress(job_id, **changes)
            else:
                current = store.read(job_id)
                current.update(changes)
                store.write(job_id, current)

        if parallel_audio:
            progress_lock = threading.RLock()
            ocr_failed = threading.Event()
            branch_progress = {"audio": 0, "ocr": 0}
            branch_stage = {
                "audio": "Đang chuẩn bị xử lý audio",
                "ocr": "Đang chuẩn bị OCR",
            }

            def report_parallel(branch: str, value: int, stage: str) -> None:
                value = max(0, min(100, int(value)))
                with progress_lock:
                    if ocr_failed.is_set():
                        return
                    # Callbacks from page workers may complete out of order.
                    branch_progress[branch] = max(branch_progress[branch], value)
                    branch_stage[branch] = stage
                    audio_progress = branch_progress["audio"]
                    ocr_progress = branch_progress["ocr"]
                    overall = min(
                        99,
                        round(audio_progress * 0.2 + ocr_progress * 0.8),
                    )
                    changes = {
                        "status": "processing",
                        "processing_phase": "audio_ocr",
                        "phase_progress": overall,
                        "progress": overall,
                        "audio_progress": audio_progress,
                        "ocr_progress": ocr_progress,
                        "audio_stage": branch_stage["audio"],
                        "ocr_stage": branch_stage["ocr"],
                        "stage": (
                            f"Audio {audio_progress}% · OCR {ocr_progress}%"
                        ),
                    }
                    if hasattr(store, "write_progress"):
                        store.write_progress(job_id, **changes)
                    else:
                        current = store.read(job_id)
                        current.update(changes)
                        store.write(job_id, current)

            def mark_ocr_failed(_exc: Exception) -> None:
                with progress_lock:
                    ocr_failed.set()

            report_parallel("audio", 0, branch_stage["audio"])
            report_parallel("ocr", 0, branch_stage["ocr"])
            with ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="audio-prepare"
            ) as audio_executor:
                audio_future = audio_executor.submit(
                    prepare_web_audio,
                    store,
                    job_id,
                    progress=lambda value, stage: report_parallel(
                        "audio", value, stage
                    ),
                )
                _run_extraction(
                    job_id,
                    input_path,
                    progress_callback=lambda value, stage: report_parallel(
                        "ocr", value, stage
                    ),
                    before_finalize=audio_future.result,
                    failure_callback=mark_ocr_failed,
                )
                # Consume the future even when OCR failed before reaching its
                # finalize barrier; otherwise an audio exception would be
                # silently discarded by ThreadPoolExecutor.shutdown().
                audio_future.result()
            return

        if has_audio:
            report_audio(0, "Đang chuẩn bị xử lý audio")
            prepare_web_audio(
                store,
                job_id,
                progress=report_audio,
            )
        _run_extraction(
            job_id,
            input_path,
            overall_progress_start=20 if has_audio else 0,
        )
    except Exception as exc:
        # The Celery task has its own retry/error boundary, while the local
        # ThreadPoolExecutor does not. Always publish a terminal state so a
        # Desktop poll cannot remain at queued/audio forever.
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


@app.post("/api/extractions", status_code=202)
async def create_extraction(
    request: Request,
    file: UploadFile = File(...),
    exam_type: Literal["listening", "reading"] = Form(...),
    audio_mode: Literal["none", "full", "question_groups"] = Form(default="none"),
    audio_manifest: str = Form(default=""),
    audio_files: list[UploadFile] = File(default=[]),
    audio: UploadFile | None = File(default=None),
    audio_full: UploadFile | None = File(default=None),
    audio_part_1: UploadFile | None = File(default=None),
    audio_part_2: UploadFile | None = File(default=None),
    audio_part_3: UploadFile | None = File(default=None),
    audio_part_4: UploadFile | None = File(default=None),
    requested_count: int | None = Form(default=None),
    no_cache: bool = Form(default=False),
) -> JSONResponse:
    identity = current_identity(request, required=False)
    owner_user_id = (
        _desktop_user_id(request)
        if settings.desktop
        else identity["user_id"] if identity else None
    )
    processing_location = "LOCAL_EDGE" if settings.desktop else "REMOTE_SERVER"
    logger.info(
        "[OCR_ROUTE] location=%s event=upload_received profile=%s client=%s filename=%s type=%s",
        processing_location,
        settings.app_profile,
        request.client.host if request.client else "unknown",
        Path(file.filename or "").name,
        exam_type,
    )
    if not settings.desktop and not settings.ocr_enabled:
        logger.warning(
            "[OCR_ROUTE] location=REMOTE_SERVER event=rejected reason=ocr-disabled"
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "OCR trên máy chủ đã tắt. Hãy bật OCR_ENABLED để xử lý tài liệu."
            ),
        )
    if not file.filename:
        raise HTTPException(status_code=400, detail="Thiếu tên file")
    if requested_count is not None and not 1 <= requested_count <= 100:
        raise HTTPException(status_code=422, detail="Số câu phải nằm trong khoảng 1–100")
    if Path(file.filename).suffix.lower() not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận file .pdf")
    if exam_type != "listening" and audio_mode != "none":
        raise HTTPException(
            status_code=422,
            detail="Chỉ đề Listening mới được cấu hình audio",
        )
    if exam_type == "listening" and audio_mode == "none":
        raise HTTPException(
            status_code=422,
            detail="Đề Listening cần Audio Full hoặc bộ audio theo câu/nhóm",
        )
    if audio_files and audio_mode != "question_groups":
        raise HTTPException(
            status_code=422,
            detail="Danh sách audio chỉ dùng với chế độ theo câu/nhóm",
        )
    if audio is not None and audio.filename and audio_full is not None and audio_full.filename:
        raise HTTPException(status_code=422, detail="Chỉ gửi một audio Full")
    full_upload = audio_full if audio_full is not None and audio_full.filename else audio
    legacy_part_uploads = [
        upload
        for upload in (audio_part_1, audio_part_2, audio_part_3, audio_part_4)
        if upload is not None and upload.filename
    ]
    if legacy_part_uploads:
        raise HTTPException(
            status_code=422,
            detail="Không còn hỗ trợ Audio theo Part 1–4. Hãy dùng Audio Full hoặc Audio theo câu/nhóm.",
        )
    audio_uploads: list[tuple[str, UploadFile, dict[str, object]]] = []
    if full_upload is not None and full_upload.filename:
        audio_uploads.append(
            ("full", full_upload, {"scope": "full", "question_numbers": []})
        )
    if audio_mode == "question_groups":
        if audio_uploads:
            raise HTTPException(
                status_code=422,
                detail="Audio theo câu/nhóm không dùng đồng thời với Audio Full",
            )
        try:
            manifest = json.loads(audio_manifest or "[]")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="Manifest audio không hợp lệ") from exc
        if not isinstance(manifest, list) or not manifest or len(manifest) > 100:
            raise HTTPException(status_code=422, detail="Manifest audio phải có từ 1 đến 100 nhóm")
        if len(manifest) != len(audio_files):
            raise HTTPException(status_code=422, detail="Số manifest không khớp số file audio")
        seen_ids: set[str] = set()
        seen_questions: set[int] = set()
        seen_file_indexes: set[int] = set()
        for entry in manifest:
            if not isinstance(entry, dict):
                raise HTTPException(status_code=422, detail="Mỗi audio phải có manifest dạng object")
            entry_id = str(entry.get("id") or "").strip()
            scope = str(entry.get("scope") or "").strip()
            raw_numbers = entry.get("question_numbers")
            file_index = entry.get("file_index")
            if (
                not entry_id
                or entry_id in seen_ids
                or len(entry_id) > 80
                or not isinstance(raw_numbers, list)
                or not raw_numbers
                or not isinstance(file_index, int)
                or file_index < 0
                or file_index >= len(audio_files)
                or file_index in seen_file_indexes
            ):
                raise HTTPException(status_code=422, detail="Manifest audio có trường không hợp lệ")
            numbers: list[int] = []
            for value in raw_numbers:
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
                    raise HTTPException(status_code=422, detail="Số câu audio phải nằm trong khoảng 1–100")
                numbers.append(value)
            if len(set(numbers)) != len(numbers) or seen_questions.intersection(numbers):
                raise HTTPException(status_code=422, detail="Các audio đang bị trùng câu hỏi")
            if scope == "question":
                if len(numbers) != 1 or numbers[0] > 31:
                    raise HTTPException(
                        status_code=422,
                        detail="Audio theo câu chỉ dùng cho câu 1–31",
                    )
            elif scope == "group":
                if len(numbers) < 2 or min(numbers) < 32 or max(numbers) > 100:
                    raise HTTPException(
                        status_code=422,
                        detail="Audio nhóm chỉ dùng cho nhóm câu 32–100",
                    )
                if numbers != list(range(min(numbers), max(numbers) + 1)):
                    raise HTTPException(status_code=422, detail="Câu trong audio nhóm phải liên tiếp")
                expected_group_starts = set(range(32, 71, 3)) | set(range(71, 101, 3))
                if numbers[0] not in expected_group_starts or numbers != list(
                    range(numbers[0], min(numbers[0] + 2, 100) + 1)
                ):
                    raise HTTPException(
                        status_code=422,
                        detail="Audio nhóm phải đúng nhóm TOEIC: 32–34, 35–37, …, 98–100",
                    )
            else:
                raise HTTPException(status_code=422, detail="Scope audio phải là question hoặc group")
            seen_ids.add(entry_id)
            seen_questions.update(numbers)
            seen_file_indexes.add(file_index)
            inferred_part = (
                "part_1" if numbers[0] <= 6 else
                "part_2" if numbers[0] <= 31 else
                "part_3" if numbers[0] <= 70 else "part_4"
            )
            audio_uploads.append(
                (
                    inferred_part,
                    audio_files[file_index],
                    {
                        "id": entry_id,
                        "scope": scope,
                        "question_numbers": numbers,
                        "group_id": entry_id if scope == "group" else None,
                    },
                )
            )
    elif audio_manifest:
        raise HTTPException(status_code=422, detail="Chỉ gửi manifest khi dùng mode audio theo câu/nhóm")
    if audio_uploads and exam_type != "listening":
        raise HTTPException(status_code=422, detail="Audio chỉ được dùng cho đề Listening")
    received_parts = {part for part, _upload, _descriptor in audio_uploads}
    if audio_mode == "full" and "full" not in received_parts:
        raise HTTPException(status_code=422, detail="Chưa chọn Audio Full")
    if any(part == "full" for part, _upload, _descriptor in audio_uploads) and len(audio_uploads) > 1:
        raise HTTPException(
            status_code=422,
            detail="Chọn Audio Full hoặc audio theo câu/nhóm, không dùng đồng thời",
        )
    total_audio_size = 0
    for part, upload, _descriptor in audio_uploads:
        extension = Path(upload.filename or "").suffix.lower()
        if extension not in ALLOWED_AUDIO_TYPES:
            raise HTTPException(
                status_code=400,
                detail="Audio phải là MP3, WAV, M4A, AAC, OGG, WebM hoặc FLAC",
            )

    digest = hashlib.sha256()
    size = 0
    staging_path: Path | None = None
    staged_audios: list[dict[str, object]] = []
    staging_paths: set[Path] = set()
    job_id: str | None = None
    # In server mode Starlette has already spooled multipart files. Reusing
    # those seekable files for MinIO removes the second scratch copy. Desktop
    # keeps the filesystem path because its local executor consumes it.
    direct_persistent_ingest = bool(
        _uses_persistent_job_objects() and settings.use_celery
    )
    try:
        if direct_persistent_ingest:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File vượt quá {MAX_BYTES // (1024 * 1024)} MB",
                    )
                digest.update(chunk)
            await file.seek(0)
        else:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".upload-",
                suffix=".pdf",
                dir=store.root,
                delete=False,
            ) as output:
                staging_path = Path(output.name)
                staging_paths.add(staging_path)
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"File vượt quá {MAX_BYTES // (1024 * 1024)} MB",
                        )
                    digest.update(chunk)
                    output.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="File PDF rỗng")

        for part, upload, descriptor in audio_uploads:
            audio_digest = hashlib.sha256()
            audio_size = 0
            audio_staging_path: Path | None = None
            extension = Path(upload.filename or "").suffix.lower()
            if direct_persistent_ingest:
                while chunk := await upload.read(1024 * 1024):
                    audio_size += len(chunk)
                    total_audio_size += len(chunk)
                    if audio_size > MAX_AUDIO_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Audio {part.replace('_', ' ').title()} vượt quá {MAX_AUDIO_BYTES // (1024 * 1024)} MB",
                    )
                    if total_audio_size > MAX_TOTAL_AUDIO_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Tổng dung lượng audio vượt quá {MAX_TOTAL_AUDIO_BYTES // (1024 * 1024)} MB",
                        )
                    audio_digest.update(chunk)
                await upload.seek(0)
            else:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=".audio-upload-",
                    suffix=extension,
                    dir=store.root,
                    delete=False,
                ) as audio_output:
                    audio_staging_path = Path(audio_output.name)
                    # Register the path before the first write. A disconnect or
                    # ENOSPC during the copy must not leak an untracked file.
                    staging_paths.add(audio_staging_path)
                    while chunk := await upload.read(1024 * 1024):
                        audio_size += len(chunk)
                        total_audio_size += len(chunk)
                        if audio_size > MAX_AUDIO_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail=f"Audio {part.replace('_', ' ').title()} vượt quá {MAX_AUDIO_BYTES // (1024 * 1024)} MB",
                            )
                        if total_audio_size > MAX_TOTAL_AUDIO_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail=f"Tổng dung lượng audio vượt quá {MAX_TOTAL_AUDIO_BYTES // (1024 * 1024)} MB",
                            )
                        audio_digest.update(chunk)
                        audio_output.write(chunk)
            if audio_size == 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Audio {part.replace('_', ' ').title()} rỗng",
                )
            staged_audios.append(
                {
                    "part": part,
                    "upload": upload,
                    "extension": extension,
                    "path": audio_staging_path,
                    "size": audio_size,
                    "hash": audio_digest.hexdigest(),
                    "descriptor": descriptor,
                }
            )

        source_hash = digest.hexdigest()
        audio_hashes = ":".join(
            f"{item['part']}:{json.dumps(item.get('descriptor') or {}, sort_keys=True)}:{item['hash']}"
            for item in staged_audios
        )
        # The requested output size is part of the cache identity. Without it,
        # uploading the same PDF once for 25 questions and again for 100 could
        # incorrectly reuse the first review/finalization state.
        file_hash = hashlib.sha256(
            f"{source_hash}:{audio_hashes}:count={requested_count or 'all'}".encode(
                "utf-8"
            )
        ).hexdigest()
        if not no_cache:
            cached = store.find_cached(
                file_hash=file_hash,
                exam_type=exam_type,
                owner_user_id=owner_user_id,
            )
            if cached:
                return JSONResponse(
                    status_code=202,
                    content={
                        "job_id": cached["job_id"],
                        "status": cached["status"],
                        "cached": True,
                        "processing_location": processing_location,
                    },
                )

        safe_filename = Path(file.filename).name
        job_id, job_dir = store.create(
            filename=safe_filename,
            exam_type=exam_type,
            file_hash=file_hash,
            owner_user_id=owner_user_id,
        )
        if hasattr(store, "set_owner"):
            store.set_owner(job_id, identity["user_id"] if identity else None)
        if requested_count is not None:
            state = store.read(job_id)
            state["requested_count"] = requested_count
            state.setdefault("metadata", {})["requested_count"] = requested_count
            store.write(job_id, state)
        input_path = job_dir / "input.pdf"
        if direct_persistent_ingest:
            await asyncio.to_thread(
                storage.put_stream,
                settings.minio_bucket_sources,
                f"jobs/{job_id}/input.pdf",
                file.file,
                length=size,
                content_type="application/pdf",
            )
        else:
            if staging_path is None:
                raise RuntimeError("Thiếu PDF staging")
            os.replace(staging_path, input_path)
            staging_paths.discard(staging_path)
            staging_path = None
        audio_refs: list[dict] = []
        for item in staged_audios:
            upload = item["upload"]
            extension = str(item["extension"])
            audio_id = f"{uuid.uuid4().hex}{extension}"
            if direct_persistent_ingest:
                await asyncio.to_thread(
                    storage.put_stream,
                    settings.minio_bucket_audio,
                    f"jobs/{job_id}/audio/{audio_id}",
                    upload.file,
                    length=int(item["size"]),
                    content_type=ALLOWED_AUDIO_TYPES[extension],
                )
            else:
                staged_audio_path = item.get("path")
                if staged_audio_path is None:
                    raise RuntimeError("Thiếu audio staging")
                destination = job_dir / "audio" / audio_id
                os.replace(Path(staged_audio_path), destination)
                staging_paths.discard(Path(staged_audio_path))
                item["path"] = None
            audio_refs.append(
                AudioRef(
                    id=audio_id,
                    url=f"/api/extractions/{job_id}/audio/{audio_id}",
                    filename=Path(upload.filename).name,
                    content_type=ALLOWED_AUDIO_TYPES[extension],
                    size=int(item["size"]),
                    part=str(item["part"]),
                    scope=str((item.get("descriptor") or {}).get("scope") or "part"),
                    question_numbers=list((item.get("descriptor") or {}).get("question_numbers") or []),
                    group_id=(item.get("descriptor") or {}).get("group_id"),
                ).model_dump()
            )
        if audio_refs:
            state = store.read(job_id)
            state["audios"] = audio_refs
            state["audio"] = next(
                (item for item in audio_refs if item["part"] == "full"),
                None,
            )
            state["metadata"]["source_file_hash"] = source_hash
            state["metadata"]["audio_file_hashes"] = {
                str(item.get("descriptor", {}).get("id") or item["part"]): str(item["hash"])
                for item in staged_audios
            }
            store.write(job_id, state)
        else:
            # Persist the source PDF before a remote worker starts.
            store.write(job_id, store.read(job_id))
    except Exception as exc:
        if job_id is not None and hasattr(store, "discard"):
            try:
                await asyncio.to_thread(store.discard, job_id)
            except Exception:
                logger.exception("UPLOAD_ROLLBACK_FAILED job=%s", job_id)
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            logger.error("UPLOAD_SCRATCH_EXHAUSTED root=%s", store.root)
            raise HTTPException(
                status_code=507,
                detail=(
                    "Máy chủ tạm hết dung lượng xử lý upload. "
                    "Vui lòng đợi ít phút rồi thử lại."
                ),
            ) from exc
        raise
    finally:
        await file.close()
        closed: set[int] = set()
        for upload in (
            audio,
            audio_full,
            audio_part_1,
            audio_part_2,
            audio_part_3,
            audio_part_4,
            *audio_files,
        ):
            if upload is not None and id(upload) not in closed:
                await upload.close()
                closed.add(id(upload))
        if staging_path is not None:
            staging_paths.add(staging_path)
        for path in staging_paths:
            path.unlink(missing_ok=True)

    if job_id is None:
        raise HTTPException(status_code=500, detail="Không tạo được job xử lý")

    if settings.use_celery:
        from ocr_tasks import process_extraction

        process_extraction.delay(job_id)
        logger.info(
            "[OCR_ROUTE] location=%s event=queued_celery job=%s", processing_location, job_id
        )
        if hasattr(store, "evict_local"):
            store.evict_local(job_id)
    else:
        EXTRACTION_EXECUTOR.submit(_run_extraction_job, job_id, input_path)
        logger.info(
            "[OCR_ROUTE] location=%s event=queued_local_executor job=%s",
            processing_location,
            job_id,
        )
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "queued",
            "cached": False,
            "processing_location": processing_location,
        },
    )


@app.get("/api/extractions/{job_id}", response_model=ExamDraft)
def get_extraction(job_id: str, request: Request) -> ExamDraft:
    _check_job_access(job_id, request)
    try:
        state = store.read(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ExamDraft.model_validate(state)


@app.get("/api/extractions/{job_id}/assets/{asset_id}")
def get_asset(job_id: str, asset_id: str, request: Request) -> Response:
    _check_job_access(job_id, request)
    if _uses_persistent_job_objects():
        try:
            state = store.read(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        known_assets = {
            str(asset.get("id") or "")
            for stimulus in state.get("stimuli") or []
            for asset in stimulus.get("assets") or []
        }
        if asset_id not in known_assets or Path(asset_id).name != asset_id:
            raise HTTPException(status_code=404, detail="Asset không thuộc job")
        return _persistent_job_object_response(
            request,
            bucket=settings.minio_bucket_assets,
            object_key=f"jobs/{job_id}/assets/{asset_id}",
            content_type="image/webp",
        )
    try:
        path = store.asset_path(job_id, asset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/api/extractions/{job_id}/audio/{audio_id}")
def get_audio(job_id: str, audio_id: str, request: Request) -> Response:
    _check_job_access(job_id, request)
    try:
        state = store.read(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audio_refs = state.get("audios") or []
    if not audio_refs and state.get("audio"):
        audio_refs = [state["audio"]]
    audio_ref = next(
        (item for item in audio_refs if item.get("id") == audio_id),
        None,
    )
    if audio_ref is None:
        raise HTTPException(status_code=404, detail="Audio không thuộc job")
    content_type = audio_ref.get("content_type") or "application/octet-stream"
    if _uses_persistent_job_objects():
        if Path(audio_id).name != audio_id:
            raise HTTPException(status_code=404, detail="Audio không thuộc job")
        return _persistent_job_object_response(
            request,
            bucket=settings.minio_bucket_audio,
            object_key=f"jobs/{job_id}/audio/{audio_id}",
            content_type=content_type,
        )
    try:
        path = store.audio_path(job_id, audio_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type=content_type,
        filename=audio_ref.get("filename") or audio_id,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.post("/api/extractions/{job_id}/answer-key-image")
def import_answer_key_image(
    job_id: str,
    request: Request,
    file: UploadFile = File(...),
) -> JSONResponse:
    _check_job_access(job_id, request, write=True)
    try:
        state = store.read(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if state["status"] not in {"review", "ready"}:
        raise HTTPException(status_code=409, detail="Job chưa sẵn sàng")

    payload = file.file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Ảnh đáp án rỗng")
    if len(payload) > MAX_ANSWER_IMAGE_BYTES:
        raise HTTPException(
            status_code=413, detail="Ảnh đáp án vượt quá 10 MB"
        )
    ocr_started = time.monotonic()
    try:
        expected_numbers = {
            int(question["number"]) for question in state.get("questions", [])
        }
        if not expected_numbers:
            raise HTTPException(
                status_code=409,
                detail="Draft chưa có câu hỏi để gán answer key.",
            )
        answers, raw_text, duplicates = extract_answer_key_image(
            bytes(payload), expected_numbers=expected_numbers
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    questions = {
        int(question["number"]): question for question in state.get("questions", [])
    }
    valid: dict[str, str] = {}
    ignored: list[str] = list(duplicates)
    for number, letter in answers.items():
        question = questions.get(number)
        # Blank text in Listening Part 1/2 is intentional and must never
        # affect answer-key import.  The permitted answer letters still come
        # from the extracted question: Part 1 is A-D; Part 2 is A-C.
        if question is None or letter not in question.get("option_letters", []):
            ignored.append(f"{number}{letter}")
            continue
        valid[str(number)] = letter
    missing = sorted(expected_numbers - {int(number) for number in valid})
    # Keep the unfiltered OCR evidence so a Reading key pasted into a
    # Listening review (or the reverse) is diagnosed instead of looking like
    # a total OCR failure.  The extractor still filters candidates to the
    # current job for data integrity.
    detail = answer_key_scope_detail(raw_text, expected_numbers) if not valid else None
    if detail:
        missing = []
    ocr_duration_ms = round((time.monotonic() - ocr_started) * 1000)
    logger.info(
        "[OCR_QUALITY] location=%s event=answer_key job=%s recognized=%s expected=%s "
        "missing=%s ignored=%s duration_ms=%s",
        "LOCAL_EDGE" if settings.desktop else "REMOTE_SERVER",
        job_id,
        len(valid),
        len(expected_numbers),
        missing or "none",
        len(ignored),
        ocr_duration_ms,
    )
    return JSONResponse(
        content={
            "answer_key": valid,
            "recognized_count": len(valid),
            "ignored": ignored,
            "missing": missing,
            "raw_text": raw_text,
            "duration_ms": ocr_duration_ms,
            "detail": detail,
        }
    )


@app.get("/api/extractions/{job_id}/pages/{page_number}")
def get_source_page(job_id: str, page_number: int, request: Request) -> Response:
    _check_job_access(job_id, request)
    if page_number < 1 or page_number > 500:
        raise HTTPException(status_code=404, detail="Trang không hợp lệ")
    if _uses_persistent_job_objects():
        try:
            state = store.read(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        page_count = int((state.get("metadata") or {}).get("page_count") or 0)
        if page_count and page_number > page_count:
            raise HTTPException(status_code=404, detail="Trang không thuộc job")
        return _persistent_job_object_response(
            request,
            bucket=settings.minio_bucket_assets,
            object_key=f"jobs/{job_id}/pages/page-{page_number:03d}.jpg",
            content_type="image/jpeg",
        )
    if hasattr(store, "page_path"):
        try:
            path = store.page_path(job_id, page_number)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        try:
            job_dir = store.job_dir(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = job_dir / "pages" / f"page-{page_number:03d}.jpg"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy trang")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.patch("/api/extractions/{job_id}/draft", response_model=ExamDraft)
def patch_draft(job_id: str, patch: DraftPatch, request: Request) -> ExamDraft:
    _check_job_access(job_id, request, write=True)
    try:
        state = store.read(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if state["status"] not in {"review", "ready"}:
        raise HTTPException(status_code=409, detail="Job chưa sẵn sàng để chỉnh sửa")

    inserted_numbers: list[int] = []
    if patch.questions is not None:
        numbers = [question.number for question in patch.questions]
        if len(numbers) != len(set(numbers)):
            raise HTTPException(status_code=422, detail="Số câu hỏi bị trùng")
        questions, inserted_numbers = ensure_question_coverage(
            state["exam_type"], patch.questions, _state_question_range(state)
        )
        state["questions"] = [
            question.model_dump() for question in questions
        ]
    else:
        # Old cached drafts may have been produced before the OCR pipeline
        # created missing-number placeholders. Repair their coverage on the
        # next safe review save without discarding any existing edits.
        questions = [Question.model_validate(item) for item in state.get("questions", [])]
        questions, inserted_numbers = ensure_question_coverage(
            state["exam_type"], questions, _state_question_range(state)
        )
        state["questions"] = [question.model_dump() for question in questions]

    if patch.stimuli is not None:
        old_stimuli = {item["id"]: item for item in state.get("stimuli", [])}
        updated: list[dict] = []
        for model in patch.stimuli:
            stimulus = model.model_dump()
            old = old_stimuli.get(stimulus["id"])
            changed_asset_ids: list[str] = []
            old_assets = {
                asset.get("id"): asset for asset in (old or {}).get("assets", [])
            }
            for asset in stimulus.get("assets", []):
                previous = old_assets.get(asset.get("id"))
                if previous is None or (
                    asset.get("bbox") != previous.get("bbox")
                    or asset.get("page") != previous.get("page")
                ):
                    changed_asset_ids.append(str(asset.get("id", "")))
            if changed_asset_ids:
                try:
                    for asset_id in changed_asset_ids:
                        changed_asset = next(
                            (
                                asset
                                for asset in stimulus.get("assets", [])
                                if str(asset.get("id") or "") == asset_id
                            ),
                            None,
                        )
                        if changed_asset is None:
                            raise ValueError("Không tìm thấy asset cần cắt lại")
                        page_number = int(changed_asset.get("page") or 0)
                        if hasattr(store, "page_path"):
                            source_page = store.page_path(job_id, page_number)
                            job_dir = source_page.parent.parent
                        else:
                            job_dir = store.job_dir(job_id)
                        stimulus = recrop_asset(
                            job_id=job_id,
                            job_dir=job_dir,
                            stimulus=stimulus,
                            asset_id=asset_id,
                        )
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
            updated.append(stimulus)
        state["stimuli"] = updated

    if patch.solutions is not None:
        try:
            state["solutions"] = validate_solutions(
                [entry.model_dump() for entry in patch.solutions],
                state["exam_type"],
            )
        except SolutionValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.issues) from exc

    state["returned_count"] = len(state.get("questions", []))
    state["status"] = "review"
    state["stage"] = (
        f"Đã thêm {len(inserted_numbers)} câu cần nhập thủ công"
        if inserted_numbers
        else "Đã lưu chỉnh sửa"
    )
    store.write(job_id, state)
    if not settings.desktop and settings.database_url:
        identity = current_identity(request, required=False)
        if identity:
            from models import ExamEditSession, utcnow
            from sqlalchemy import select

            with session_scope() as session:
                active_sessions = session.scalars(
                    select(ExamEditSession).where(
                        ExamEditSession.editor_user_id == identity["user_id"],
                        ExamEditSession.status == "active",
                    )
                ).all()
                for active in active_sessions:
                    if job_id in (active.job_ids or {}).values():
                        active.expires_at = utcnow() + timedelta(hours=2)
                        active.updated_at = utcnow()
                        break
    return ExamDraft.model_validate(state)


@app.post("/api/extractions/{job_id}/manual-stimulus", response_model=ExamDraft)
def add_manual_stimulus(
    job_id: str,
    payload: ManualStimulusRequest,
    request: Request,
) -> ExamDraft:
    """Replace a question's auto crop with a teacher-selected source-page crop."""
    _check_job_access(job_id, request, write=True)
    try:
        state = store.read(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if state["status"] not in {"review", "ready"}:
        raise HTTPException(status_code=409, detail="Job chưa sẵn sàng để chỉnh sửa")

    numbers = sorted(set(payload.question_numbers))
    if len(numbers) != len(payload.question_numbers):
        raise HTTPException(status_code=422, detail="Số câu hỏi bị trùng")
    known_numbers = {int(question["number"]) for question in state.get("questions", [])}
    unknown = sorted(set(numbers) - known_numbers)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Không tìm thấy câu hỏi: {', '.join(str(number) for number in unknown)}",
        )
    try:
        if hasattr(store, "page_path"):
            source_page = store.page_path(job_id, payload.page)
            job_dir = source_page.parent.parent
        else:
            job_dir = store.job_dir(job_id)
        stimulus = create_manual_stimulus(
            job_id=job_id,
            job_dir=job_dir,
            page_number=payload.page,
            bbox=tuple(payload.bbox),
            question_numbers=numbers,
            title=payload.title,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    selected = set(numbers)
    # A question has one active stimulus.  Detach it from the old automatic
    # group (and drop empty groups from the draft) before assigning the crop.
    # The original source page remains retained, so this operation is fully
    # recoverable by creating another manual crop during review.
    remaining_stimuli: list[dict] = []
    for existing in state.get("stimuli", []):
        kept_numbers = [
            int(number)
            for number in existing.get("question_numbers", [])
            if int(number) not in selected
        ]
        if not kept_numbers:
            continue
        existing["question_numbers"] = kept_numbers
        remaining_stimuli.append(existing)
    state["stimuli"] = remaining_stimuli + [stimulus]
    state["questions"] = [
        {
            **question,
            "stimulus_id": stimulus["id"]
            if int(question["number"]) in selected
            else question.get("stimulus_id"),
        }
        for question in state.get("questions", [])
    ]
    state["returned_count"] = len(state["questions"])
    state["status"] = "review"
    state["stage"] = "Đã thêm ảnh cắt thủ công"
    store.write(job_id, state)
    return ExamDraft.model_validate(state)


def _select_grouped_questions(
    questions: list[Question], requested_count: int, shuffle: bool
) -> list[Question]:
    units: list[list[Question]] = []
    by_group: dict[str, list[Question]] = {}
    order: list[str] = []
    for question in sorted(questions, key=lambda item: item.number):
        key = question.group_id or f"q-{question.number}"
        if key not in by_group:
            by_group[key] = []
            order.append(key)
        by_group[key].append(question)
    units = [by_group[key] for key in order]
    if shuffle:
        random.shuffle(units)
    # Audio/stimulus grouping is metadata, not a reason to exceed the count
    # the teacher explicitly requested.  A group can therefore be partially
    # selected at the boundary (its shared audio/passage remains referenced
    # by the selected questions).
    return [question for unit in units for question in unit][:requested_count]


@app.post("/api/extractions/{job_id}/finalize", response_model=FinalExam)
def finalize_extraction(
    job_id: str, request: FinalizeRequest, http_request: Request
) -> FinalExam:
    _check_job_access(job_id, http_request, write=True)
    try:
        state = store.read(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if state["status"] not in {"review", "ready"}:
        raise HTTPException(status_code=409, detail="Job chưa sẵn sàng")

    questions = [Question.model_validate(item) for item in state["questions"]]
    questions, _inserted_numbers = ensure_question_coverage(
        state["exam_type"], questions, _state_question_range(state)
    )
    by_number = {question.number: question for question in questions}
    for raw_number, raw_letter in request.answer_key.items():
        try:
            number = int(raw_number)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"Số câu không hợp lệ: {raw_number}"
            ) from exc
        question = by_number.get(number)
        if not question:
            raise HTTPException(status_code=422, detail=f"Không tồn tại câu {number}")
        letter = raw_letter.strip().upper()
        if letter not in question.option_letters:
            if letter in {"A", "B", "C", "D"}:
                if not question.options:
                    question.options = {}
                if letter not in question.options:
                    question.options[letter] = ""
            else:
                raise HTTPException(
                    status_code=422,
                    detail=f"Đáp án {letter} không hợp lệ cho câu {number}",
                )
        question.correct = letter

    unresolved_numbers = sorted(
        question.number
        for question in questions
        if "question_missing" in question.issues
        or "options_missing" in question.issues
    )
    if unresolved_numbers:
        raise HTTPException(
            status_code=422,
            detail=(
                "Cần nhập thủ công nội dung/phương án cho câu: "
                + ", ".join(str(number) for number in unresolved_numbers)
                + "."
            ),
        )

    total = len(questions)
    requested_count = min(
        request.count
        if request.count is not None
        else int(state.get("requested_count") or total),
        total,
    )
    selected = _select_grouped_questions(questions, requested_count, request.shuffle)
    referenced_stimuli = {
        question.stimulus_id for question in selected if question.stimulus_id
    }
    stimuli = [
        Stimulus.model_validate(item)
        for item in state["stimuli"]
        if item["id"] in referenced_stimuli
    ]
    selected_numbers = {question.number for question in selected}
    try:
        solutions = validate_solutions(state.get("solutions") or [], state["exam_type"])
    except SolutionValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.issues) from exc
    selected_solutions = [
        entry
        for entry in solutions
        if set(entry["question_numbers"]).issubset(selected_numbers)
    ]

    state["questions"] = [question.model_dump() for question in questions]
    state["status"] = "ready"
    state["stage"] = "Đề đã sẵn sàng"
    state["requested_count"] = requested_count
    state["returned_count"] = len(selected)
    store.write(job_id, state)

    audio_models = [
        AudioRef.model_validate(item) for item in (state.get("audios") or [])
    ]
    if not audio_models and state.get("audio"):
        audio_models = [AudioRef.model_validate(state["audio"])]
    target_title = (request.title or Path(state["filename"]).stem).strip()
    if settings.desktop:
        ds = _desktop_store(http_request)
        existing_exams = ds.list_exams()
        if any(
            item.get("title", "").strip().lower() == target_title.lower()
            and item.get("client_exam_id") != request.client_exam_id
            for item in existing_exams
        ):
            raise HTTPException(status_code=409, detail=f"Tên đề thi '{target_title}' đã tồn tại. Vui lòng chọn tên khác.")
    elif settings.database_url:
        identity = current_identity(http_request, required=False)
        if identity:
            from database import session_scope
            from models import Exam
            from sqlalchemy import select, func
            with session_scope() as session:
                is_pending_full_test_component = bool(
                    request.is_full_test_component
                    or (
                        target_title == "Listening Component"
                        and state.get("exam_type") == "listening"
                    )
                )
                # A teacher can have more than one unfinished Full Test (or
                # resume from another browser). Component titles are internal
                # staging labels, not user-facing exam names, so an older
                # component with the same label must not block finalization.
                if is_pending_full_test_component:
                    existing = None
                elif identity["role"] in {"teacher", "admin"}:
                    existing = session.scalar(
                        select(Exam).where(
                            Exam.shared_title_key
                            == teacher_scoped_title_key(identity["user_id"], target_title),
                            Exam.deleted_at.is_(None),
                        )
                    )
                else:
                    existing = session.scalar(
                        select(Exam).where(
                            Exam.owner_user_id == identity["user_id"],
                            func.lower(Exam.title) == target_title.lower(),
                            Exam.deleted_at.is_(None),
                        )
                    )
                if existing and existing.job_id != job_id:
                    raise HTTPException(status_code=409, detail=f"Tên đề thi '{target_title}' đã tồn tại. Vui lòng chọn tên khác.")

    result = FinalExam(
        job_id=job_id,
        exam_type=state["exam_type"],
        requested_count=requested_count,
        returned_count=len(selected),
        total=total,
        questions=selected,
        stimuli=stimuli,
        audio=next((item for item in audio_models if item.part == "full"), None),
        audios=audio_models,
        solutions=selected_solutions,
        title=target_title,
        category=request.category,
        client_exam_id=request.client_exam_id,
    )
    if settings.desktop:
        desktop_store = _desktop_store(http_request)
        asset_paths: dict[str, tuple[Path, str, str]] = {}
        for stimulus in stimuli:
            for asset in stimulus.assets:
                asset_paths[asset.id] = (
                    store.asset_path(job_id, asset.id),
                    "stimulus",
                    "image/webp",
                )
        for audio_ref in audio_models:
            asset_paths[audio_ref.id] = (
                store.audio_path(job_id, audio_ref.id),
                "audio",
                audio_ref.content_type,
            )
        source_component = str(state.get("exam_type") or "main")
        source_pdf = store.job_dir(job_id) / "input.pdf"
        if source_pdf.is_file():
            asset_paths[f"source-{source_component}.pdf"] = (
                source_pdf,
                "source",
                "application/pdf",
            )
        client_exam_id = desktop_store.save_exam(
            result.model_dump(),
            title=result.title or "Exam",
            category=request.category,
            asset_paths=asset_paths,
        )
        result.client_exam_id = client_exam_id
        result.sync_status = "pending"
    elif settings.database_url:
        identity = current_identity(http_request, required=False)
        exam_id = persist_final_exam(
            result.model_dump(),
            job_id=job_id,
            owner_user_id=identity["user_id"] if identity else None,
            title=target_title,
            category=request.category,
            is_full_test_component=request.is_full_test_component,
        )
        result.exam_id = exam_id
        result.title = target_title
        if exam_id:
            # Store the durable id in the payload as well.
            with session_scope() as session:
                from models import Exam

                exam = session.get(Exam, exam_id)
                if exam is not None:
                    exam.payload = result.model_dump()
    return result


@app.get("/api/desktop/exams")
def desktop_exams(request: Request) -> dict[str, object]:
    desktop_store = _desktop_store(request)
    desktop_store.repair_legacy_split_exams()
    desktop_store.normalize_exams()
    return {"items": desktop_store.list_exams()}


class DesktopCombineRequest(BaseModel):
    listening_exam_id: str
    reading_exam_id: str
    title: str
    category: str = ""
    target_exam_id: str | None = None


@app.post("/api/desktop/exams/combine")
def desktop_combine_exams(
    body: DesktopCombineRequest, request: Request
) -> dict[str, object]:
    try:
        return _desktop_store(request).combine_exams(
            body.listening_exam_id,
            body.reading_exam_id,
            title=body.title.strip() or "TOEIC Full Test",
            category=body.category.strip(),
            target_exam_id=body.target_exam_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class DesktopFullTestFinalizeRequest(BaseModel):
    listening_job_id: str
    reading_job_id: str
    title: str
    category: str = ""


@app.post("/api/desktop/exams/{client_exam_id}/edit")
def desktop_open_full_test_edit(
    client_exam_id: str, request: Request
) -> dict[str, object]:
    local = _desktop_store(request)
    try:
        jobs = local.create_edit_jobs(
            client_exam_id,
            store,
            owner_user_id=_desktop_user_id(request),
        )
        manifest = local.manifest(client_exam_id)
        return {
            "client_exam_id": client_exam_id,
            "title": manifest["title"],
            "category": manifest["category"],
            "component_job_ids": jobs,
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy Full Test") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/desktop/exams/{client_exam_id}/edit/finalize")
def desktop_finalize_full_test_edit(
    client_exam_id: str, body: DesktopFullTestFinalizeRequest, request: Request
) -> dict[str, object]:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    from desktop_store import DesktopStore

    try:
        listening = store.read(body.listening_job_id)
        reading = store.read(body.reading_job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Phiên chỉnh sửa đã hết hạn") from exc
    if listening.get("exam_type") != "listening" or reading.get("exam_type") != "reading":
        raise HTTPException(status_code=422, detail="Sai thứ tự Listening/Reading")
    listening_numbers = {
        int(item.get("number", 0)) for item in listening.get("questions") or []
    }
    reading_numbers = {
        int(item.get("number", 0)) for item in reading.get("questions") or []
    }
    if listening_numbers != set(range(1, 101)) or reading_numbers != set(
        range(101, 201)
    ):
        raise HTTPException(status_code=422, detail="Full Test phải có đủ 200 câu")

    questions = sorted(
        (listening.get("questions") or []) + (reading.get("questions") or []),
        key=lambda item: int(item["number"]),
    )
    stimuli = (listening.get("stimuli") or []) + (reading.get("stimuli") or [])
    solutions = (listening.get("solutions") or []) + (reading.get("solutions") or [])
    audios = listening.get("audios") or (
        [listening["audio"]] if listening.get("audio") else []
    )
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Tên đề không được để trống")
    payload = {
        "schema_version": 2,
        "job_id": f"{body.listening_job_id}+{body.reading_job_id}",
        "component_job_ids": {
            "listening": body.listening_job_id,
            "reading": body.reading_job_id,
        },
        "exam_type": "combined",
        "requested_count": 200,
        "returned_count": 200,
        "total": 200,
        "questions": questions,
        "stimuli": stimuli,
        "audio": next((item for item in audios if item.get("part") == "full"), None),
        "audios": audios,
        "solutions": validate_solutions(solutions, "combined"),
        "title": title,
        "category": body.category.strip(),
        "client_exam_id": client_exam_id,
        "sync_status": "pending",
    }
    asset_paths: dict[str, tuple[Path, str, str]] = {}
    for job_id, state in (
        (body.listening_job_id, listening),
        (body.reading_job_id, reading),
    ):
        for stimulus in state.get("stimuli") or []:
            for asset in stimulus.get("assets") or []:
                asset_paths[asset["id"]] = (
                    store.asset_path(job_id, asset["id"]),
                    "stimulus",
                    "image/webp",
                )
        for audio in state.get("audios") or []:
            asset_paths[audio["id"]] = (
                store.audio_path(job_id, audio["id"]),
                "audio",
                audio["content_type"],
            )
        source_pdf = store.job_dir(job_id) / "input.pdf"
        if source_pdf.is_file():
            component = str(state.get("exam_type") or "main")
            asset_paths[f"source-{component}.pdf"] = (
                source_pdf,
                "source",
                "application/pdf",
            )
    local = _desktop_store(request)
    try:
        local.manifest(client_exam_id)
        local.save_exam(
            payload,
            title=title,
            category=body.category.strip(),
            asset_paths=asset_paths,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy Full Test") from exc
    return payload


@app.get("/api/desktop/sync/pending")
def desktop_pending_sync(request: Request) -> dict[str, object]:
    local = _desktop_store(request)
    return {
        "items": [
            {"client_exam_id": client_id}
            for client_id in local.claim_pending()
        ]
    }


@app.delete("/api/desktop/exams/{client_exam_id}")
def desktop_delete_local_exam(
    client_exam_id: str, request: Request
) -> dict[str, bool]:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    try:
        _desktop_store(request).delete_local_exam(client_exam_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề local") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/api/desktop/classrooms/cache")
def desktop_cached_classrooms(request: Request) -> dict[str, object]:
    return {"items": _desktop_store(request).cached_classrooms()}


@app.post("/api/desktop/classrooms/cache")
def desktop_cache_classrooms(
    body: dict[str, object], request: Request
) -> dict[str, bool]:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    from desktop_store import DesktopStore

    items = body.get("items")
    if not isinstance(items, list) or len(items) > 100:
        raise HTTPException(status_code=422, detail="Danh sách lớp không hợp lệ")
    _desktop_store(request).cache_classrooms(
        [item for item in items if isinstance(item, dict)]
    )
    return {"ok": True}


@app.get("/api/desktop/exams/{client_exam_id}/assets/{asset_id}")
def desktop_exam_asset(
    client_exam_id: str, asset_id: str, request: Request
) -> FileResponse:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    from desktop_store import DesktopStore

    try:
        path = _desktop_store(request).asset_path(client_exam_id, asset_id)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy asset") from exc
    return FileResponse(path)


@app.get("/api/desktop/policies/{policy_key}")
def desktop_get_policy(policy_key: str, request: Request) -> dict[str, str]:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    from desktop_store import DesktopStore
    return _desktop_store(request).get_policy(policy_key)


@app.get("/api/desktop/tags")
def desktop_list_tags(request: Request) -> dict[str, list[str]]:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    from desktop_store import DesktopStore
    return {"items": _desktop_store(request).list_tags()}


@app.post("/api/desktop/tags")
def desktop_create_tag(body: dict[str, str], request: Request) -> dict[str, str]:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    from desktop_store import DesktopStore
    name = _desktop_store(request).add_tag(body.get("name", ""))
    return {"name": name}


@app.get("/api/desktop/attempts/history")
def desktop_list_attempts(request: Request) -> dict[str, list[dict]]:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    from desktop_store import DesktopStore
    return {"items": _desktop_store(request).list_attempts()}


@app.post("/api/desktop/attempts/history")
def desktop_save_attempt(body: dict, request: Request) -> dict:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    from desktop_store import DesktopStore
    return _desktop_store(request).save_attempt(body)


class DesktopSyncRequest(BaseModel):
    access_token: str
    category: str = ""
    classroom_ids: list[str] = Field(default_factory=list, max_length=100)


class DesktopReconcileRequest(BaseModel):
    access_token: str


class DesktopPublicationQueueRequest(BaseModel):
    classroom_ids: list[str] = Field(min_length=1, max_length=100)


class DesktopEpochRequest(BaseModel):
    data_epoch: str = Field(min_length=36, max_length=64)


@app.post("/api/desktop/data-epoch")
def desktop_apply_data_epoch(
    body: DesktopEpochRequest, request: Request
) -> dict[str, object]:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    local = _desktop_store(request)
    try:
        quarantined = local.ensure_data_epoch(body.data_epoch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if quarantined:
        desktop_root = Path(settings.desktop_data_dir).resolve()
        shared_jobs = (desktop_root / "jobs").resolve()
        if shared_jobs.parent == desktop_root and shared_jobs.exists():
            quarantine_root = desktop_root / "quarantine"
            quarantine_root.mkdir(parents=True, exist_ok=True)
            destination = quarantine_root / (
                f"jobs-{int(time.time())}-{uuid.uuid4().hex[:8]}"
            )
            shutil.move(str(shared_jobs), str(destination))
            shared_jobs.mkdir(parents=True, exist_ok=True)
            for item in sorted(destination.rglob("*"), reverse=True):
                try:
                    item.chmod(0o500 if item.is_dir() else 0o400)
                except OSError:
                    pass
            destination.chmod(0o500)
    return {"data_epoch": local.data_epoch(), "quarantined": quarantined}


def _remote_error(response: httpx.Response, fallback: str) -> str:
    """Return an actionable remote API error without exposing credentials."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("code") or fallback)[:1000]
    return str(detail).strip()[:1000] if detail else fallback


@app.post("/api/desktop/sync/reconcile")
def desktop_reconcile_sync(
    body: DesktopReconcileRequest, request: Request
) -> dict[str, object]:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    headers = {
        "Authorization": f"Bearer {body.access_token}",
        "X-Examify-Desktop-Version": "0.1.6",
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=10)) as client:
            response = client.get(
                f"{settings.remote_api_url}/api/v1/desktop/sync/reconcile",
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=_remote_error(exc.response, "Máy chủ từ chối reconcile Desktop."),
        ) from exc
    except (httpx.RequestError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="Chưa kết nối được máy chủ để reconcile dữ liệu"
        ) from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise HTTPException(status_code=502, detail="Payload reconcile không hợp lệ")
    return _desktop_store(request).reconcile(
        [item for item in items if isinstance(item, dict)]
    )


@app.post("/api/desktop/exams/{client_exam_id}/publications")
def desktop_queue_publications(
    client_exam_id: str, body: DesktopPublicationQueueRequest, request: Request
) -> dict[str, object]:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    from desktop_store import DesktopStore

    local = _desktop_store(request)
    try:
        local.manifest(client_exam_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề local") from exc
    local.queue_publications(client_exam_id, body.classroom_ids)
    return {
        "ok": True,
        "items": local.pending_publications(client_exam_id),
    }


@app.get("/api/desktop/exams/{client_exam_id}/publications")
def desktop_publication_status(
    client_exam_id: str, request: Request
) -> dict[str, object]:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    from desktop_store import DesktopStore

    return {
        "items": _desktop_store(request).publication_statuses(client_exam_id)
    }


@app.post("/api/desktop/exams/{client_exam_id}/sync")
def desktop_sync_exam(
    client_exam_id: str, body: DesktopSyncRequest, request: Request
) -> dict[str, object]:
    if not settings.desktop:
        raise HTTPException(status_code=404, detail="Desktop API không khả dụng")
    from desktop_store import DesktopStore

    local = _desktop_store(request)
    headers = {
        "Authorization": f"Bearer {body.access_token}",
        "X-Examify-Desktop-Version": "0.1.6",
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(120, connect=15)) as client:
            epoch_response = client.get(
                f"{settings.remote_api_url}/api/v1/system/data-epoch",
                headers=headers,
            )
            epoch_response.raise_for_status()
            data_epoch = str(epoch_response.json().get("data_epoch") or "")
            local.ensure_data_epoch(data_epoch)
            if body.category:
                try:
                    local.set_category(client_exam_id, body.category)
                except KeyError as exc:
                    raise HTTPException(
                        status_code=404,
                        detail="Đề local thuộc data epoch cũ và đã được quarantine",
                    ) from exc
            try:
                manifest = local.manifest(client_exam_id)
            except KeyError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="Đề local thuộc data epoch cũ và đã được quarantine",
                ) from exc
            if body.classroom_ids:
                local.queue_publications(client_exam_id, body.classroom_ids)
            response = client.post(
                f"{settings.remote_api_url}/api/v1/desktop/sync/exams",
                json=manifest,
                headers=headers,
            )
            response.raise_for_status()
            sync = response.json()
            uploaded = set(sync.get("uploaded_assets") or [])
            for asset in manifest["assets"]:
                if asset["asset_id"] in uploaded:
                    continue
                path = local.asset_path(client_exam_id, asset["asset_id"])
                with path.open("rb") as source:
                    upload = client.put(
                        f"{settings.remote_api_url}/api/v1/desktop/sync/exams/"
                        f"{sync['sync_id']}/assets/{asset['asset_id']}",
                        headers=headers,
                        files={
                            "file": (
                                asset["filename"],
                                source,
                                asset["content_type"],
                            )
                        },
                    )
                upload.raise_for_status()
            complete = client.post(
                f"{settings.remote_api_url}/api/v1/desktop/sync/exams/"
                f"{sync['sync_id']}/complete",
                headers=headers,
            )
            complete.raise_for_status()
            payload = complete.json()
        revision = payload.get("revision")
        if not isinstance(revision, int) or revision < 1:
            raise HTTPException(
                status_code=502, detail="Máy chủ chưa trả revision của đề"
            )
        local.mark_synced(client_exam_id, payload["exam_id"], revision)
        publication_results: list[dict[str, object]] = []
        pending_publications = local.pending_publications(client_exam_id)
        if pending_publications:
            with httpx.Client(timeout=httpx.Timeout(45, connect=10)) as client:
                for publication in pending_publications:
                    classroom_id = str(publication["classroom_id"])
                    try:
                        response = client.post(
                            f"{settings.remote_api_url}/api/v1/teacher/exams/"
                            f"{payload['exam_id']}/class-publications",
                            json={"classroom_ids": [classroom_id]},
                            headers=headers,
                        )
                        response.raise_for_status()
                    except httpx.HTTPError as exc:
                        detail = (
                            _remote_error(
                                exc.response,
                                "Máy chủ từ chối Public đề tới lớp.",
                            )
                            if isinstance(exc, httpx.HTTPStatusError)
                            else "Không kết nối được máy chủ để Public đề tới lớp."
                        )
                        local.mark_publication(client_exam_id, classroom_id, error=detail)
                        publication_results.append(
                            {"classroom_id": classroom_id, "status": "failed", "error": detail}
                        )
                    else:
                        local.mark_publication(client_exam_id, classroom_id)
                        publication_results.append(
                            {"classroom_id": classroom_id, "status": "synced"}
                        )
        return {**payload, "publications": publication_results}
    except httpx.HTTPStatusError as exc:
        detail = _remote_error(
            exc.response,
            "Máy chủ từ chối đồng bộ đề; dữ liệu vẫn được giữ trên máy.",
        )
        conflict_code = ""
        try:
            remote_detail = exc.response.json().get("detail")
            if isinstance(remote_detail, dict):
                conflict_code = str(remote_detail.get("code") or "")
        except (ValueError, AttributeError):
            pass
        if exc.response.status_code == 409 and conflict_code in {
            "exam_revision_conflict",
            "exam_deleted",
        }:
            local.mark_conflict(client_exam_id, detail)
        else:
            local.mark_failed(client_exam_id, detail)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=detail,
        ) from exc
    except (httpx.RequestError, OSError) as exc:
        local.mark_failed(client_exam_id, str(exc))
        raise HTTPException(
            status_code=503,
            detail="Chưa thể đồng bộ; dữ liệu vẫn được giữ trên máy",
        ) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
