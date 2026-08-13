"""Low-cardinality Prometheus metrics safe for multi-worker Uvicorn."""

from __future__ import annotations

import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)


HTTP_REQUESTS = Counter(
    "examify_http_requests_total",
    "HTTP requests by method, route template and response status.",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "examify_http_request_duration_seconds",
    "Application request duration by route template.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5, 10),
)
HTTP_IN_FLIGHT = Gauge(
    "examify_http_requests_in_flight",
    "Requests currently executing.",
    multiprocess_mode="livesum",
)
DB_POOL_SIZE = Gauge(
    "examify_db_pool_size",
    "Configured SQLAlchemy pool connections across API workers.",
    multiprocess_mode="livesum",
)
DB_POOL_CHECKED_OUT = Gauge(
    "examify_db_pool_checked_out",
    "SQLAlchemy connections checked out across API workers.",
    multiprocess_mode="livesum",
)
DB_POOL_OVERFLOW = Gauge(
    "examify_db_pool_overflow",
    "SQLAlchemy overflow connections across API workers.",
    multiprocess_mode="livesum",
)


def route_template(request: object) -> str:
    scope = getattr(request, "scope", {})
    route = scope.get("route") if isinstance(scope, dict) else None
    path = getattr(route, "path", None)
    return str(path or "unmatched")[:200]


def observe_pool(engine: object | None) -> None:
    if engine is None:
        return
    pool = getattr(engine, "pool", None)
    if pool is None:
        return
    DB_POOL_SIZE.set(float(getattr(pool, "size", lambda: 0)()))
    DB_POOL_CHECKED_OUT.set(float(getattr(pool, "checkedout", lambda: 0)()))
    DB_POOL_OVERFLOW.set(float(max(0, getattr(pool, "overflow", lambda: 0)())))


def render_metrics() -> tuple[bytes, str]:
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry), CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST
