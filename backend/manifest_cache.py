"""Bounded cache for immutable, answer-sanitized exam manifests."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from redis import Redis

from config import settings


class ManifestCache:
    def __init__(self) -> None:
        self._redis: Redis | None = None
        self._retry_after = 0.0
        self._memory: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}
        self._ttl = max(60, settings.exam_manifest_cache_ttl_seconds)
        self._max_local = max(1, min(settings.exam_manifest_cache_max_local, 128))

    def _client(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.15,
                socket_timeout=0.15,
                decode_responses=True,
            )
        return self._redis

    def _memory_get(self, key: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            item = self._memory.get(key)
            if item is None:
                return None
            expires_at, encoded = item
            if expires_at <= now:
                self._memory.pop(key, None)
                return None
            self._memory.move_to_end(key)
            return encoded

    def _memory_put(self, key: str, encoded: str) -> None:
        with self._lock:
            self._memory[key] = (time.monotonic() + self._ttl, encoded)
            self._memory.move_to_end(key)
            while len(self._memory) > self._max_local:
                self._memory.popitem(last=False)

    def get_or_build(
        self,
        key: str,
        builder: Callable[[], dict[str, Any]],
        *,
        namespace: str = "exam-manifest:v1",
    ) -> dict[str, Any]:
        cache_key = f"{namespace}:{key}"
        lock_key = f"{cache_key}:build-lock"
        deadline = time.monotonic() + max(
            0.5, settings.manifest_singleflight_wait_seconds
        )

        def read_cached() -> str | None:
            encoded = self._memory_get(cache_key)
            if encoded is not None:
                return encoded
            if time.monotonic() < self._retry_after:
                return None
            try:
                encoded = self._client().get(cache_key)
                if encoded:
                    self._memory_put(cache_key, encoded)
                return encoded
            except Exception:
                self._retry_after = time.monotonic() + 5
                self._redis = None
                return None

        encoded = read_cached()
        if encoded is None:
            with self._lock:
                event = self._inflight.get(cache_key)
                owner = event is None
                if owner:
                    event = threading.Event()
                    self._inflight[cache_key] = event

            if not owner:
                # A second request in this worker waits for the first builder;
                # it then re-reads Redis so separate Uvicorn workers converge
                # on the same immutable JSON as well.
                event.wait(timeout=max(0.1, deadline - time.monotonic()))
                encoded = read_cached()
                if encoded is None and time.monotonic() < deadline:
                    encoded = self.get_or_build(
                        key, builder, namespace=namespace
                    ).copy()
                    return encoded
            else:
                distributed_token: str | None = None
                try:
                    encoded = read_cached()
                    if encoded is None and time.monotonic() < self._retry_after:
                        # Redis is unavailable; local single-flight still
                        # protects this worker and the owner must build before
                        # waking waiters.
                        encoded = json.dumps(
                            builder(), ensure_ascii=False, separators=(",", ":")
                        )
                    elif encoded is None:
                        try:
                            distributed_token = uuid.uuid4().hex
                            acquired = self._client().set(
                                lock_key,
                                distributed_token,
                                nx=True,
                                ex=max(10, min(self._ttl, 30)),
                            )
                        except Exception:
                            acquired = True
                            self._retry_after = time.monotonic() + 5
                            self._redis = None
                        if not acquired:
                            while time.monotonic() < deadline:
                                time.sleep(0.05)
                                encoded = read_cached()
                                if encoded is not None:
                                    break
                        if encoded is None:
                            encoded = json.dumps(
                                builder(), ensure_ascii=False, separators=(",", ":")
                            )
                            if time.monotonic() >= self._retry_after:
                                try:
                                    self._client().setex(cache_key, self._ttl, encoded)
                                except Exception:
                                    self._retry_after = time.monotonic() + 5
                                    self._redis = None
                finally:
                    if encoded is not None:
                        # Publish before waking local waiters. Otherwise a
                        # waiter can observe the cleared inflight entry and
                        # become a second builder during the tiny handoff.
                        self._memory_put(cache_key, encoded)
                    if distributed_token and self._redis is not None:
                        try:
                            if self._client().get(lock_key) == distributed_token:
                                self._client().delete(lock_key)
                        except Exception:
                            pass
                    with self._lock:
                        current = self._inflight.pop(cache_key, None)
                        if current is not None:
                            current.set()

        if encoded is None:
            encoded = json.dumps(builder(), ensure_ascii=False, separators=(",", ":"))
        self._memory_put(cache_key, encoded)
        # Every request receives its own mutable copy for signed asset URLs.
        return json.loads(encoded)


manifest_cache = ManifestCache()
