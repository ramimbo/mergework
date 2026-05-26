from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import Request
from fastapi.responses import Response

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
API_DOCS_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "connect-src 'self'; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
    "object-src 'none'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "worker-src 'self' blob:"
)
API_DOCS_PATHS = {"/api/docs", "/api/redoc"}


def route_head_as_get(request: Request) -> str:
    original_method = str(request.scope["method"])
    if original_method == "HEAD":
        request.scope["method"] = "GET"
    return original_method


def restore_request_method(request: Request, original_method: str) -> None:
    request.scope["method"] = original_method


def response_for_original_method(response: Response, original_method: str) -> Response:
    if original_method != "HEAD":
        return response
    headers = dict(response.headers)
    headers["content-length"] = "0"
    return Response(
        status_code=response.status_code,
        headers=headers,
        media_type=response.media_type,
    )


def security_headers_for_path(path: str) -> dict[str, str]:
    headers = dict(SECURITY_HEADERS)
    if path in API_DOCS_PATHS:
        headers["Content-Security-Policy"] = API_DOCS_CSP
    return headers


def request_was_forwarded_https(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",", 1)[0].strip().lower() == "https"
    return request.url.scheme == "https"


def preserve_forwarded_https_redirect(request: Request, response: Response) -> None:
    if response.status_code not in {307, 308} or not request_was_forwarded_https(request):
        return
    location = response.headers.get("location")
    if not location:
        return
    parsed = urlsplit(location)
    if parsed.scheme != "http" or parsed.netloc != request.url.netloc:
        return
    response.headers["location"] = urlunsplit(
        ("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment)
    )


def apply_http_response_safety(request: Request, response: Response) -> Response:
    preserve_forwarded_https_redirect(request, response)
    for name, value in security_headers_for_path(request.url.path).items():
        response.headers.setdefault(name, value)
    return response


async def run_with_head_as_get(request: Request, call_next: Any) -> Response:
    original_method = route_head_as_get(request)
    try:
        response = await call_next(request)
    finally:
        restore_request_method(request, original_method)
    return response_for_original_method(response, original_method)
