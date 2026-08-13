"""Short-lived Redis cache for the durable User/Device identity join."""

from __future__ import annotations

import json
import time
from typing import Any

from config import settings


IDENTITY_TTL_SECONDS = 30


class IdentityCache:
    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis = redis_client
        self._disabled_until = 0.0
        if self._redis is None and settings.redis_url:
            try:
                import redis

                self._redis = redis.Redis.from_url(
                    settings.redis_url,
                    socket_connect_timeout=0.15,
                    socket_timeout=0.15,
                    health_check_interval=30,
                    decode_responses=True,
                )
            except Exception:
                self._redis = None

    @staticmethod
    def _device_key(device_id: str) -> str:
        return f"examify:identity:device:{device_id}"

    @staticmethod
    def _user_devices_key(user_id: str) -> str:
        return f"examify:identity:user:{user_id}:devices"

    def get(self, device_id: str) -> dict[str, Any] | None:
        if self._redis is None or time.monotonic() < self._disabled_until:
            return None
        try:
            raw = self._redis.get(self._device_key(device_id))
            value = json.loads(raw) if raw else None
            return value if isinstance(value, dict) else None
        except Exception:
            self._disabled_until = time.monotonic() + 5.0
            return None

    def put(self, value: dict[str, Any]) -> None:
        if self._redis is None or time.monotonic() < self._disabled_until:
            return
        device_id = str(value["device_id"])
        user_id = str(value["user_id"])
        try:
            pipeline = self._redis.pipeline(transaction=True)
            pipeline.setex(
                self._device_key(device_id),
                IDENTITY_TTL_SECONDS,
                json.dumps(value, separators=(",", ":")),
            )
            pipeline.sadd(self._user_devices_key(user_id), device_id)
            pipeline.expire(self._user_devices_key(user_id), IDENTITY_TTL_SECONDS * 4)
            pipeline.execute()
        except Exception:
            self._disabled_until = time.monotonic() + 5.0
            return

    def invalidate_device(self, device_id: str) -> None:
        if self._redis is None or time.monotonic() < self._disabled_until:
            return
        try:
            self._redis.delete(self._device_key(device_id))
        except Exception:
            self._disabled_until = time.monotonic() + 5.0
            return

    def invalidate_user(self, user_id: str) -> None:
        if self._redis is None or time.monotonic() < self._disabled_until:
            return
        user_key = self._user_devices_key(user_id)
        try:
            device_ids = self._redis.smembers(user_key)
            keys = [self._device_key(str(device_id)) for device_id in device_ids]
            self._redis.delete(*keys, user_key) if keys else self._redis.delete(user_key)
        except Exception:
            self._disabled_until = time.monotonic() + 5.0
            return


identity_cache = IdentityCache()
