"""Short-lived Redis cache for immutable MinIO authorization metadata."""

from __future__ import annotations

import json
import time
from typing import Any

from redis import Redis

from config import settings


class MediaAuthorizationCache:
    def __init__(self) -> None:
        self._redis: Redis | None = None
        self._retry_after = 0.0
        self._ttl = max(30, min(settings.media_auth_cache_ttl_seconds, 300))

    def _client(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.15,
                socket_timeout=0.15,
                decode_responses=True,
            )
        return self._redis

    def get(self, token_hash: str) -> dict[str, Any] | None:
        if time.monotonic() < self._retry_after:
            return None
        try:
            value = self._client().get(f"media-auth:v1:{token_hash}")
            return json.loads(value) if value else None
        except Exception:
            self._redis = None
            self._retry_after = time.monotonic() + 5
            return None

    def put(
        self,
        token_hash: str,
        metadata: dict[str, Any],
        *,
        member_id: str,
        assignment_id: str,
    ) -> None:
        if time.monotonic() < self._retry_after:
            return
        cache_key = f"media-auth:v1:{token_hash}"
        member_key = f"media-auth-member:v1:{member_id}"
        assignment_key = f"media-auth-assignment:v1:{assignment_id}"
        try:
            pipeline = self._client().pipeline(transaction=False)
            pipeline.setex(cache_key, self._ttl, json.dumps(metadata, separators=(",", ":")))
            pipeline.sadd(member_key, cache_key)
            pipeline.expire(member_key, self._ttl)
            pipeline.sadd(assignment_key, cache_key)
            pipeline.expire(assignment_key, self._ttl)
            pipeline.execute()
        except Exception:
            self._redis = None
            self._retry_after = time.monotonic() + 5

    def _invalidate(self, index_key: str) -> None:
        try:
            client = self._client()
            keys = list(client.smembers(index_key))
            if keys:
                client.delete(*keys)
            client.delete(index_key)
        except Exception:
            self._redis = None
            self._retry_after = time.monotonic() + 5

    def invalidate_member(self, member_id: str) -> None:
        self._invalidate(f"media-auth-member:v1:{member_id}")

    def invalidate_assignment(self, assignment_id: str) -> None:
        self._invalidate(f"media-auth-assignment:v1:{assignment_id}")


media_auth_cache = MediaAuthorizationCache()
