from starlette.requests import Request
from starlette.responses import Response

from auth_service import set_access_cookie


def _request(scheme: str, forwarded: str = "") -> Request:
    headers = []
    if forwarded:
        headers.append((b"x-forwarded-proto", forwarded.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "scheme": scheme,
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
        }
    )


def test_ip_http_cookie_is_not_secure() -> None:
    response = Response()
    set_access_cookie(response, "access-token", request=_request("http"))
    assert "; Secure" not in response.headers["set-cookie"]


def test_forwarded_https_cookie_remains_secure() -> None:
    response = Response()
    set_access_cookie(
        response,
        "access-token",
        request=_request("http", forwarded="https"),
    )
    assert "; Secure" in response.headers["set-cookie"]
