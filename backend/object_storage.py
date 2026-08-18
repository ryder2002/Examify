"""Private MinIO object storage with safe object keys."""

from __future__ import annotations

import mimetypes
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from minio import Minio
from minio.commonconfig import CopySource
from minio.datatypes import PostPolicy

from config import settings


class ObjectStorage:
    def __init__(self) -> None:
        if not settings.minio_endpoint:
            raise RuntimeError("MINIO_ENDPOINT chưa được cấu hình")
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    def ensure_buckets(self) -> None:
        for bucket in {
            settings.minio_bucket_sources,
            settings.minio_bucket_assets,
            settings.minio_bucket_audio,
            settings.minio_bucket_answers,
            settings.minio_bucket_guides,
        }:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)

    def put_file(
        self, bucket: str, key: str, path: Path, content_type: str | None = None
    ) -> None:
        safe_key = self.safe_key(key)
        self.client.fput_object(
            bucket,
            safe_key,
            str(path),
            content_type=content_type or mimetypes.guess_type(path.name)[0],
        )

    def get_file(self, bucket: str, key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.fget_object(bucket, self.safe_key(key), str(destination))
        return destination

    def put_bytes(
        self, bucket: str, key: str, payload: bytes, content_type: str
    ) -> None:
        self.client.put_object(
            bucket,
            self.safe_key(key),
            BytesIO(payload),
            length=len(payload),
            content_type=content_type,
        )

    def put_stream(
        self,
        bucket: str,
        key: str,
        source: BinaryIO,
        *,
        length: int,
        content_type: str,
    ) -> None:
        """Upload an already-spooled request body without making another copy.

        FastAPI/Starlette has materialized large multipart parts before the
        endpoint runs. Reusing that seekable stream avoids a second PDF/audio
        file in API scratch space while preserving MinIO as the durable source
        of truth consumed by Celery workers.
        """

        if length < 0:
            raise ValueError("Kích thước object không hợp lệ")
        source.seek(0)
        self.client.put_object(
            bucket,
            self.safe_key(key),
            source,
            length=length,
            content_type=content_type,
        )

    def remove_prefix(self, bucket: str, prefix: str) -> None:
        for item in self.client.list_objects(bucket, prefix=self.safe_key(prefix), recursive=True):
            self.client.remove_object(bucket, item.object_name)

    def remove_object(self, bucket: str, key: str) -> None:
        self.client.remove_object(bucket, self.safe_key(key))

    def copy_object(
        self,
        bucket: str,
        source_key: str,
        destination_key: str,
    ) -> None:
        self.client.copy_object(
            bucket,
            self.safe_key(destination_key),
            CopySource(bucket, self.safe_key(source_key)),
        )

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        return [
            item.object_name
            for item in self.client.list_objects(
                bucket, prefix=self.safe_key(prefix), recursive=True
            )
        ]

    def presigned_internal_redirect(
        self,
        bucket: str,
        key: str,
        prefix: str,
        *,
        method: str = "GET",
        expires: timedelta = timedelta(hours=1),
    ) -> str:
        """Build an nginx internal URI that remains authenticated to MinIO.

        ``internal`` protects the nginx location from direct browser access,
        but it does not authenticate nginx to a private S3 bucket.  Preserve
        the SigV4 query generated for MinIO's internal host; nginx is configured
        to send the matching ``Host: minio:9000`` header upstream.
        """

        signed = self.client.get_presigned_url(
            method.upper(),
            bucket,
            self.safe_key(key),
            expires=expires,
        )
        parsed = urlsplit(signed)
        if not parsed.path or not parsed.query:
            raise RuntimeError("MinIO không tạo được signed internal URL")
        return f"{prefix.rstrip('/')}{parsed.path}?{parsed.query}"

    def presigned_browser_post(
        self,
        bucket: str,
        key: str,
        *,
        content_type: str,
        minimum_size: int,
        maximum_size: int,
        expires: timedelta = timedelta(minutes=15),
    ) -> dict[str, object]:
        """Return a same-origin, exact-key POST policy for direct upload.

        The browser posts to Nginx, which streams the signed multipart request
        to private MinIO. The policy—not a public bucket—authorizes one object,
        one MIME and a narrow content-length range for at most 15 minutes.
        """

        if minimum_size < 0 or maximum_size < minimum_size:
            raise ValueError("Khoảng kích thước upload không hợp lệ")
        safe_key = self.safe_key(key)
        policy = PostPolicy(bucket, datetime.now(timezone.utc) + expires)
        policy.add_equals_condition("key", safe_key)
        policy.add_equals_condition("Content-Type", content_type)
        policy.add_content_length_range_condition(minimum_size, maximum_size)
        fields = self.client.presigned_post_policy(policy)
        fields["key"] = safe_key
        fields["Content-Type"] = content_type
        return {
            "url": f"/client-uploads/{bucket}",
            "method": "POST",
            "fields": fields,
            "expires_in_seconds": int(expires.total_seconds()),
        }

    @staticmethod
    def safe_key(key: str) -> str:
        normalized = key.strip().lstrip("/")
        if not normalized or ".." in Path(normalized).parts:
            raise ValueError("Object key không hợp lệ")
        return normalized


storage = ObjectStorage() if settings.minio_endpoint else None
