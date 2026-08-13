"""Ephemeral Redis presence for active attempts.

Presence is intentionally best-effort and reconstructable. PostgreSQL remains
the authority for answer, deadline and submission state.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from config import settings


logger = logging.getLogger(__name__)
PRESENCE_TTL_SECONDS = 75


class PresenceStore:
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
                )
            except Exception:
                self._redis = None

    @staticmethod
    def _key(attempt_id: str) -> str:
        return f"examify:presence:attempt:{attempt_id}"

    def put(self, attempt_id: str, payload: dict[str, Any]) -> bool:
        if self._redis is None or time.monotonic() < self._disabled_until:
            return False
        try:
            self._redis.setex(
                self._key(attempt_id),
                PRESENCE_TTL_SECONDS,
                json.dumps(payload, separators=(",", ":"), default=str),
            )
            return True
        except Exception as exc:
            self._disabled_until = time.monotonic() + 5.0
            logger.warning("PRESENCE_REDIS_WRITE_FAILED reason=%s", type(exc).__name__)
            return False

    def get_many(self, attempt_ids: list[str]) -> dict[str, dict[str, Any]]:
        if (
            self._redis is None
            or not attempt_ids
            or time.monotonic() < self._disabled_until
        ):
            return {}
        try:
            pipeline = self._redis.pipeline(transaction=False)
            for attempt_id in attempt_ids:
                pipeline.get(self._key(attempt_id))
            result: dict[str, dict[str, Any]] = {}
            for attempt_id, raw in zip(attempt_ids, pipeline.execute()):
                if not raw:
                    continue
                decoded = json.loads(raw)
                if isinstance(decoded, dict):
                    result[attempt_id] = decoded
            return result
        except Exception as exc:
            self._disabled_until = time.monotonic() + 5.0
            logger.warning("PRESENCE_REDIS_READ_FAILED reason=%s", type(exc).__name__)
            return {}


presence_store = PresenceStore()
