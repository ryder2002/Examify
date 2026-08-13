"""Application settings shared by the API and OCR workers."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_profile: str = os.getenv("APP_PROFILE", "server").strip().lower()
    desktop_secret: str = os.getenv("DESKTOP_SECRET", "")
    desktop_data_dir: str = os.getenv("DESKTOP_DATA_DIR", "")
    remote_api_url: str = os.getenv(
        "REMOTE_API_URL", "https://exam.congnhat.online"
    ).rstrip("/")
    database_url: str = os.getenv("DATABASE_URL", "")
    database_pool_size: int = _int("DB_POOL_SIZE", 5)
    database_max_overflow: int = _int("DB_MAX_OVERFLOW", 5)
    database_pool_timeout_seconds: int = _int("DB_POOL_TIMEOUT_SECONDS", 10)
    database_pool_recycle_seconds: int = _int("DB_POOL_RECYCLE_SECONDS", 1800)
    database_connect_timeout_seconds: int = _int("DB_CONNECT_TIMEOUT_SECONDS", 5)
    database_statement_timeout_ms: int = _int("DB_STATEMENT_TIMEOUT_MS", 30_000)
    database_lock_timeout_ms: int = _int("DB_LOCK_TIMEOUT_MS", 5_000)
    database_idle_transaction_timeout_ms: int = _int(
        "DB_IDLE_TRANSACTION_TIMEOUT_MS", 60_000
    )
    auth_verify_concurrency: int = _int("AUTH_VERIFY_CONCURRENCY", 4)
    manifest_singleflight_wait_seconds: float = float(
        os.getenv("EXAM_MANIFEST_SINGLEFLIGHT_WAIT_SECONDS", "8")
    )
    scratch_min_free_bytes: int = _int(
        "SCRATCH_MIN_FREE_BYTES", 512 * 1024 * 1024
    )
    scratch_min_free_percent: int = _int("SCRATCH_MIN_FREE_PERCENT", 5)
    presence_write_interval_seconds: int = _int(
        "PRESENCE_WRITE_INTERVAL_SECONDS", 60
    )
    exam_manifest_cache_ttl_seconds: int = _int(
        "EXAM_MANIFEST_CACHE_TTL_SECONDS", 600
    )
    exam_manifest_cache_max_local: int = _int(
        "EXAM_MANIFEST_CACHE_MAX_LOCAL", 32
    )
    media_auth_cache_ttl_seconds: int = _int(
        "MEDIA_AUTH_CACHE_TTL_SECONDS", 300
    )
    identity_websocket_interval_seconds: int = _int(
        "IDENTITY_WEBSOCKET_INTERVAL_SECONDS", 15
    )
    pending_component_retention_hours: int = _int(
        "PENDING_COMPONENT_RETENTION_HOURS", 24
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    minio_endpoint: str = os.getenv("MINIO_ENDPOINT", "")
    minio_access_key: str = os.getenv("MINIO_ACCESS_KEY", "")
    minio_secret_key: str = os.getenv("MINIO_SECRET_KEY", "")
    minio_secure: bool = _bool("MINIO_SECURE")
    minio_bucket_sources: str = os.getenv("MINIO_BUCKET_SOURCES", "examify-sources")
    minio_bucket_assets: str = os.getenv("MINIO_BUCKET_ASSETS", "examify-assets")
    minio_bucket_audio: str = os.getenv("MINIO_BUCKET_AUDIO", "examify-audio")
    minio_bucket_answers: str = os.getenv("MINIO_BUCKET_ANSWERS", "examify-answers")
    minio_bucket_guides: str = os.getenv("MINIO_BUCKET_GUIDES", "examify-guides")
    minio_public_url: str = os.getenv("MINIO_PUBLIC_URL", "").rstrip("/")
    minio_accel_redirect_prefix: str = os.getenv(
        "MINIO_ACCEL_REDIRECT_PREFIX", ""
    ).rstrip("/")
    class_asset_token_minutes: int = _int("CLASS_ASSET_TOKEN_MINUTES", 180)
    guide_image_max_bytes: int = _int("GUIDE_IMAGE_MAX_BYTES", 10 * 1024 * 1024)
    guide_video_max_bytes: int = _int("GUIDE_VIDEO_MAX_BYTES", 200 * 1024 * 1024)
    jwt_secret: str = os.getenv("JWT_SECRET", "change-me-in-production")
    token_export_secret: str = os.getenv("TOKEN_EXPORT_SECRET", "")
    access_token_minutes: int = int(os.getenv("ACCESS_TOKEN_MINUTES", "30"))
    refresh_token_days: int = int(os.getenv("REFRESH_TOKEN_DAYS", "90"))
    min_desktop_auth_version: str = os.getenv(
        "MIN_DESKTOP_AUTH_VERSION", "0.1.0"
    ).strip()
    auth_required: bool = _bool("AUTH_REQUIRED")
    use_celery: bool = _bool("USE_CELERY")
    ocr_enabled: bool = _bool("OCR_ENABLED", True)
    tesseract_lang: str = os.getenv("TESSERACT_LANG", "eng").strip() or "eng"
    tesseract_data_dir: str = os.getenv("TESSERACT_DATA_DIR", "").strip()
    tesseract_timeout_seconds: int = _int("TESSERACT_TIMEOUT_SECONDS", 90)
    admin_email: str = os.getenv("ADMIN_EMAIL", "admin@local")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "")
    cors_allowed_origins: str = os.getenv("CORS_ALLOWED_ORIGINS", "")
    dictionary_api_url: str = os.getenv(
        "DICTIONARY_API_URL", "https://api.dictionaryapi.dev"
    ).rstrip("/")
    dictionary_translation_url: str = os.getenv(
        "DICTIONARY_TRANSLATION_URL", "https://api.mymemory.translated.net"
    ).rstrip("/")
    dictionary_examples_url: str = os.getenv(
        "DICTIONARY_EXAMPLES_URL", "https://api.tatoeba.org"
    ).rstrip("/")
    max_pdf_pages: int = _int("MAX_PDF_PAGES", 500)
    dictionary_http_timeout_seconds: int = _int(
        "DICTIONARY_HTTP_TIMEOUT_SECONDS", 8
    )
    dictionary_cache_ttl_seconds: int = _int(
        "DICTIONARY_CACHE_TTL_SECONDS", 7 * 24 * 60 * 60
    )
    dictionary_negative_cache_ttl_seconds: int = _int(
        "DICTIONARY_NEGATIVE_CACHE_TTL_SECONDS", 60 * 60
    )
    mymemory_contact_email: str = os.getenv("MYMEMORY_CONTACT_EMAIL", "").strip()
    local_dictionary_path: str = os.getenv(
        "LOCAL_DICTIONARY_PATH", "/dictionary-data/en_vi.sqlite3"
    ).strip()

    @property
    def persistence_enabled(self) -> bool:
        return self.app_profile in {"server", "loadtest"} and bool(
            self.database_url and self.minio_endpoint
        )

    @property
    def desktop(self) -> bool:
        return self.app_profile == "desktop"


settings = Settings()
