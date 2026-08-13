from types import SimpleNamespace

import pytest

from rate_limit import RateLimitExceeded, RateLimiter


def make_request(path: str, method: str = "POST", host: str = "10.0.0.2"):
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        method=method,
        client=SimpleNamespace(host=host),
        headers={},
        state=SimpleNamespace(),
    )


def test_teacher_has_a_high_budget_but_is_not_exempt() -> None:
    limiter = RateLimiter(use_redis=False, max_local_entries=128)
    request = make_request("/api/v1/attempts/attempt-1/answers")
    for _ in range(500):
        decision = limiter.check(request, {"user_id": "teacher-1", "role": "Teacher"})
        assert decision.exempt is False


def test_student_limit_is_bounded_by_user_scope() -> None:
    limiter = RateLimiter(use_redis=False, max_local_entries=128)
    request = make_request("/api/v1/attempts/attempt-1/answers")
    identity = {"user_id": "student-1", "role": "student"}
    for _ in range(180):
        limiter.check(request, identity)
    with pytest.raises(RateLimitExceeded):
        limiter.check(request, identity)


def test_health_is_not_limited() -> None:
    limiter = RateLimiter(use_redis=False)
    request = make_request("/health", method="GET")
    for _ in range(1000):
        decision = limiter.check(request)
        assert decision.exempt is False
        assert decision.limit == 0


def test_sync_uses_attempt_burst_without_blocking_school_nat() -> None:
    limiter = RateLimiter(use_redis=False, max_local_entries=2048)
    for index in range(300):
        request = make_request(
            f"/api/v1/student/attempts/attempt-{index}/sync", method="PATCH"
        )
        limiter.check(
            request,
            {"user_id": f"student-{index}", "role": "student"},
        )

    one_attempt = make_request(
        "/api/v1/student/attempts/burst-attempt/sync", method="PATCH"
    )
    identity = {"user_id": "burst-student", "role": "student"}
    for _ in range(4):
        limiter.check(one_attempt, identity)
    with pytest.raises(RateLimitExceeded):
        limiter.check(one_attempt, identity)


def test_login_allows_nat_storm_but_throttles_one_email() -> None:
    limiter = RateLimiter(use_redis=False, max_local_entries=2048)
    for index in range(300):
        request = make_request("/api/v1/auth/login")
        request.state.rate_limit_subject = f"student-{index}@school.test"
        limiter.check(request)

    repeated = make_request("/api/v1/auth/login", host="10.0.0.3")
    repeated.state.rate_limit_subject = "same@school.test"
    for _ in range(5):
        limiter.check(repeated)
    with pytest.raises(RateLimitExceeded):
        limiter.check(repeated)
