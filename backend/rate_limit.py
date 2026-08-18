"""Bounded, role-aware API rate limiting.

Redis uses one atomic Lua token bucket per scope so bursts do not align on a
fixed-window boundary. A bounded in-process token bucket keeps one worker
protected during a short Redis outage. Teacher traffic has a larger budget but
is never completely exempt from upload or CPU-heavy route protection.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import threading
import time
import math
from dataclasses import dataclass
from typing import Any

from config import settings


@dataclass(frozen=True)
class RateLimitPolicy:
    name: str
    window_seconds: int
    ip_limit: int
    user_limit: int
    token_limit: int
    subject_limit: int | None = None
    attempt_limit: int | None = None
    subject_burst: int | None = None
    attempt_burst: int | None = None


@dataclass(frozen=True)
class RateLimitDecision:
    exempt: bool = False
    limit: int = 0
    remaining: int = 0
    retry_after: int = 0


class RateLimitExceeded(Exception):
    def __init__(self, policy: RateLimitPolicy, retry_after: int) -> None:
        self.policy = policy
        self.retry_after = max(1, retry_after)
        super().__init__(f"rate limit exceeded for {policy.name}")


_SYNC_PATTERN = re.compile(
    r"^/api/v1/(?:student/|class-session/)?attempts/[^/]+/sync$"
)
_SUBMIT_PATTERN = re.compile(
    r"^/api/v1/(?:student/|class-session/)?attempts/[^/]+/submit$"
)
_ATTEMPT_ID_PATTERN = re.compile(
    r"^/api/v1/(?:student/|class-session/)?attempts/([^/]+)(?:/|$)"
)

_SYNC_POLICY = RateLimitPolicy(
    "attempt-sync", 60, 10_000, 30, 30,
    attempt_limit=12, attempt_burst=4,
)
_SUBMIT_POLICY = RateLimitPolicy(
    "attempt-submit", 60, 10_000, 30, 30,
    attempt_limit=10, attempt_burst=3,
)

_POLICIES = (
    (
        re.compile(r"^/api/v1/(?:desktop/)?auth/login$"),
        # The IP budget allows legitimate users behind one school/NAT to log
        # in during a class window; the email subject remains separately
        # bounded to contain password-guessing against one account.
        RateLimitPolicy(
            "auth-login", 60, 600, 30, 30,
            subject_limit=5, subject_burst=5,
        ),
    ),
    (
        re.compile(r"^/api/v1/(?:desktop/)?auth/(?:register|refresh)$"),
        RateLimitPolicy("auth-session", 60, 30, 30, 30),
    ),
    (
        re.compile(r"^/api/v1/(?:activations/redeem|desktop/activate)$"),
        RateLimitPolicy("activation", 60, 20, 20, 20),
    ),
    (
        re.compile(r"^/api/extractions$|^/api/v1/admin/guide-media/upload$"),
        RateLimitPolicy("upload", 60, 12, 30, 30),
    ),
    (
        re.compile(r"^/api/v1/client-extractions(?:/|$)"),
        RateLimitPolicy("client-extraction", 60, 300, 30, 30),
    ),
    (
        re.compile(r"^/api/v1/solution-imports/validate$"),
        RateLimitPolicy("solution-validate", 60, 300, 30, 30),
    ),
    (
        re.compile(r"/answer-key-image$"),
        RateLimitPolicy("answer-key", 60, 30, 60, 60),
    ),
    (
        re.compile(r"^/api/v1/public-tests/"),
        RateLimitPolicy("public-test", 60, 60, 60, 60),
    ),
    (
        re.compile(r"^/api/v1/(?:student/)?attempts/|^/api/v1/class-session/"),
        RateLimitPolicy(
            "exam-legacy", 60, 10_000, 180, 180,
            attempt_limit=180, attempt_burst=180,
        ),
    ),
    (
        re.compile(r"^/api/v1/(?:dictionary|tags)"),
        RateLimitPolicy("lookup", 60, 180, 120, 120),
    ),
    (
        re.compile(r"/assets/|/class-assets/|/guide-media/"),
        RateLimitPolicy("media", 60, 5000, 3000, 3000),
    ),
)

_DEFAULT_POLICY = RateLimitPolicy("api-default", 60, 600, 300, 300)
_SKIP_PATHS = {
    "/health",
    "/health/live",
    "/health/ready",
    "/docs",
    "/openapi.json",
}


def is_teacher(identity: dict[str, Any] | None) -> bool:
    """Return true for the durable Teacher role, independent of casing."""

    return bool(identity and str(identity.get("role") or "").casefold() == "teacher")


def policy_for(path: str, method: str) -> RateLimitPolicy | None:
    if method.upper() == "OPTIONS" or path in _SKIP_PATHS or not path.startswith("/api/"):
        return None
    normalized_method = method.upper()
    if normalized_method == "PATCH" and _SYNC_PATTERN.match(path):
        return _SYNC_POLICY
    if normalized_method == "POST" and _SUBMIT_PATTERN.match(path):
        return _SUBMIT_POLICY
    for pattern, policy in _POLICIES:
        if pattern.search(path):
            return policy
    return _DEFAULT_POLICY


_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_per_ms = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local ttl_ms = tonumber(ARGV[4])
local values = redis.call('HMGET', key, 'tokens', 'updated_ms')
local tokens = tonumber(values[1])
local updated_ms = tonumber(values[2])
if tokens == nil then tokens = capacity end
if updated_ms == nil then updated_ms = now_ms end
tokens = math.min(capacity, tokens + math.max(0, now_ms - updated_ms) * refill_per_ms)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', key, 'tokens', tokens, 'updated_ms', now_ms)
redis.call('PEXPIRE', key, ttl_ms)
return {allowed, math.floor(tokens), tostring(tokens)}
"""


class RateLimiter:
    """Redis token-bucket limiter with a bounded local fallback."""

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        use_redis: bool = True,
        max_local_entries: int = 4096,
    ) -> None:
        self._lock = threading.Lock()
        self._local: dict[str, tuple[float, float]] = {}
        self._max_local_entries = max(128, max_local_entries)
        self._redis = redis_client
        self._redis_disabled_until = 0.0
        if self._redis is None and use_redis and settings.redis_url:
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
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _client_ip(request: Any) -> str:
        headers = getattr(request, "headers", {})
        forwarded = str(headers.get("x-real-ip") or "").strip()
        if not forwarded:
            forwarded = str(headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
        if forwarded:
            try:
                ipaddress.ip_address(forwarded)
                return forwarded
            except ValueError:
                pass
        client = getattr(request, "client", None)
        return str(getattr(client, "host", None) or "unknown")

    def _scopes(self, request: Any, identity: dict[str, Any] | None) -> list[tuple[str, str]]:
        scopes: list[tuple[str, str]] = [("ip", self._client_ip(request))]
        if identity and identity.get("user_id"):
            scopes.append(("user", str(identity["user_id"])))
        state = getattr(request, "state", None)
        subject = str(getattr(state, "rate_limit_subject", "") or "")
        if subject:
            scopes.append(("subject", subject))
        attempt_match = _ATTEMPT_ID_PATTERN.match(str(request.url.path))
        if attempt_match:
            scopes.append(("attempt", attempt_match.group(1)))
        device_key = str(getattr(request, "headers", {}).get("x-examify-device-key") or "")
        if device_key:
            scopes.append(("token", device_key))
        authorization = str(getattr(request, "headers", {}).get("authorization") or "")
        if authorization.lower().startswith("bearer "):
            scopes.append(("token", authorization[7:].strip()))
        return scopes

    @staticmethod
    def _scope_limit(policy: RateLimitPolicy, scope_type: str) -> int:
        if scope_type == "subject":
            return policy.subject_limit or policy.user_limit
        if scope_type == "attempt":
            return policy.attempt_limit or policy.user_limit
        if scope_type == "user":
            return policy.user_limit
        if scope_type == "token":
            return policy.token_limit
        return policy.ip_limit

    @staticmethod
    def _scope_capacity(
        policy: RateLimitPolicy, scope_type: str, *, multiplier: int = 1
    ) -> int:
        if scope_type == "subject" and policy.subject_burst is not None:
            return policy.subject_burst * multiplier
        if scope_type == "attempt" and policy.attempt_burst is not None:
            return policy.attempt_burst * multiplier
        return RateLimiter._scope_limit(policy, scope_type) * multiplier

    def _redis_consume(
        self,
        key: str,
        *,
        capacity: int,
        refill_limit: int,
        window: int,
        now: float,
    ) -> tuple[bool, int, int]:
        if self._redis is None:
            raise RuntimeError("redis unavailable")
        if time.monotonic() < self._redis_disabled_until:
            raise RuntimeError("redis temporarily disabled")
        refill_per_ms = refill_limit / (window * 1_000)
        ttl_ms = max(window * 2_000, math.ceil(capacity / refill_per_ms))
        result = self._redis.eval(
            _TOKEN_BUCKET_LUA,
            1,
            f"examify:ratelimit:{key}",
            capacity,
            refill_per_ms,
            int(now * 1_000),
            ttl_ms,
        )
        allowed = bool(int(result[0]))
        remaining = max(0, int(result[1]))
        tokens = float(result[2])
        retry_after = (
            0
            if allowed
            else max(1, math.ceil((1 - tokens) / (refill_limit / window)))
        )
        return allowed, remaining, retry_after

    def _local_consume(
        self,
        key: str,
        *,
        capacity: int,
        refill_limit: int,
        window: int,
        now: float,
    ) -> tuple[bool, int, int]:
        refill_per_second = refill_limit / window
        with self._lock:
            if len(self._local) > self._max_local_entries:
                self._local = {
                    entry_key: entry
                    for entry_key, entry in self._local.items()
                    if now - entry[0] <= window * 2
                }
            previous = self._local.get(key)
            tokens = float(capacity)
            if previous:
                tokens = min(
                    float(capacity),
                    previous[1] + max(0.0, now - previous[0]) * refill_per_second,
                )
            allowed = tokens >= 1
            if allowed:
                tokens -= 1
            self._local[key] = (now, tokens)
            retry_after = (
                0
                if allowed
                else max(1, math.ceil((1 - tokens) / refill_per_second))
            )
            return allowed, max(0, math.floor(tokens)), retry_after

    def check(
        self,
        request: Any,
        identity: dict[str, Any] | None = None,
    ) -> RateLimitDecision:
        policy = policy_for(str(request.url.path), str(request.method))
        if policy is None:
            return RateLimitDecision()

        now = time.time()
        teacher_multiplier = 4 if is_teacher(identity) else 1
        minimum_remaining = policy.ip_limit
        minimum_limit = policy.ip_limit
        for scope_type, raw_scope in self._scopes(request, identity):
            limit = self._scope_limit(policy, scope_type) * teacher_multiplier
            capacity = self._scope_capacity(
                policy, scope_type, multiplier=teacher_multiplier
            )
            lane = "teacher" if teacher_multiplier > 1 else "standard"
            key = self._digest(
                f"{policy.name}:{lane}:{scope_type}:{raw_scope}"
            )
            try:
                allowed, remaining, retry_after = self._redis_consume(
                    key,
                    capacity=capacity,
                    refill_limit=limit,
                    window=policy.window_seconds,
                    now=now,
                )
            except Exception:
                self._redis_disabled_until = time.monotonic() + 5.0
                allowed, remaining, retry_after = self._local_consume(
                    key,
                    capacity=capacity,
                    refill_limit=limit,
                    window=policy.window_seconds,
                    now=now,
                )
            minimum_remaining = min(minimum_remaining, remaining)
            minimum_limit = min(minimum_limit, capacity)
            if not allowed:
                raise RateLimitExceeded(policy, retry_after)

        return RateLimitDecision(
            limit=minimum_limit,
            remaining=minimum_remaining,
            retry_after=0,
        )

    def reset_local_for_tests(self) -> None:
        """Clear only process-local buckets between isolated test cases."""

        with self._lock:
            self._local.clear()


rate_limiter = RateLimiter()
