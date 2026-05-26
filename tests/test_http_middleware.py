from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response

from app.http_middleware import (
    apply_http_response_safety,
    preserve_forwarded_https_redirect,
    response_for_original_method,
    restore_request_method,
    route_head_as_get,
    security_headers_for_path,
)


def _request(
    path: str = "/",
    *,
    method: str = "GET",
    scheme: str = "http",
    host: str = "mrwk.ltclab.site",
    headers: dict[str, str] | None = None,
) -> Request:
    request_headers = {"host": host}
    request_headers.update(headers or {})
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in request_headers.items()
    ]
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": scheme,
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": raw_headers,
            "server": (host, 443 if scheme == "https" else 80),
            "client": ("testclient", 50000),
        }
    )


def test_security_headers_for_path_keeps_docs_csp_separate() -> None:
    regular_headers = security_headers_for_path("/")
    docs_headers = security_headers_for_path("/api/docs")

    assert "https://cdn.jsdelivr.net" not in regular_headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in regular_headers["Content-Security-Policy"]
    assert "https://cdn.jsdelivr.net" in docs_headers["Content-Security-Policy"]
    assert "https://fonts.googleapis.com" in docs_headers["Content-Security-Policy"]
    assert docs_headers["X-Content-Type-Options"] == "nosniff"


def test_forwarded_https_redirect_preserves_same_host_only() -> None:
    request = _request(headers={"x-forwarded-proto": "https"})
    response = Response(status_code=307, headers={"location": "http://mrwk.ltclab.site/docs"})

    preserve_forwarded_https_redirect(request, response)

    assert response.headers["location"] == "https://mrwk.ltclab.site/docs"

    external = Response(status_code=307, headers={"location": "http://example.test/docs"})
    preserve_forwarded_https_redirect(request, external)
    assert external.headers["location"] == "http://example.test/docs"


def test_head_response_helpers_route_as_get_and_restore_empty_body() -> None:
    request = _request(method="HEAD")

    original_method = route_head_as_get(request)
    assert original_method == "HEAD"
    assert request.scope["method"] == "GET"
    restore_request_method(request, original_method)
    assert request.scope["method"] == "HEAD"

    get_response = Response("payload", media_type="text/plain", headers={"x-route": "ok"})
    head_response = response_for_original_method(get_response, original_method)

    assert head_response.status_code == 200
    assert head_response.headers["x-route"] == "ok"
    assert head_response.headers["content-length"] == "0"
    assert head_response.body == b""


def test_apply_http_response_safety_sets_docs_csp_and_redirect_scheme() -> None:
    request = _request("/api/docs", headers={"x-forwarded-proto": "https"})
    response = Response(status_code=307, headers={"location": "http://mrwk.ltclab.site/api/docs"})

    updated = apply_http_response_safety(request, response)

    assert updated is response
    assert response.headers["location"] == "https://mrwk.ltclab.site/api/docs"
    assert "https://cdn.jsdelivr.net" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
