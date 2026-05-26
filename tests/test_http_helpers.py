from __future__ import annotations

from unittest.mock import MagicMock

from app.http_helpers import (
    _host_without_port,
    _is_ltc_lab_host,
    _preserve_forwarded_https_redirect,
    _request_was_forwarded_https,
)


def _mock_request(headers: dict[str, str] | None = None, scheme: str = "http", netloc: str = "example.com") -> MagicMock:
    request = MagicMock()
    request.headers = headers or {}
    request.url.scheme = scheme
    request.url.netloc = netloc
    return request


def _mock_response(status_code: int, location: str | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.headers = {}
    if location:
        response.headers["location"] = location
    return response


class TestRequestWasForwardedHttps:
    def test_forwarded_proto_https(self) -> None:
        request = _mock_request({"x-forwarded-proto": "https"})
        assert _request_was_forwarded_https(request) is True

    def test_forwarded_proto_http(self) -> None:
        request = _mock_request({"x-forwarded-proto": "http"})
        assert _request_was_forwarded_https(request) is False

    def test_no_forwarded_proto_https_scheme(self) -> None:
        request = _mock_request(scheme="https")
        assert _request_was_forwarded_https(request) is True

    def test_no_forwarded_proto_http_scheme(self) -> None:
        request = _mock_request(scheme="http")
        assert _request_was_forwarded_https(request) is False

    def test_multi_proto_first_wins(self) -> None:
        request = _mock_request({"x-forwarded-proto": "https, http"})
        assert _request_was_forwarded_https(request) is True


class TestPreserveForwardedHttpsRedirect:
    def test_non_307_308_returns_early(self) -> None:
        request = _mock_request()
        response = _mock_response(302, "http://example.com/page")
        _preserve_forwarded_https_redirect(request, response)
        assert response.headers["location"] == "http://example.com/page"

    def test_307_upgrades_to_https(self) -> None:
        request = _mock_request({"x-forwarded-proto": "https"}, netloc="ltclab.site")
        response = _mock_response(307, "http://ltclab.site/page")
        _preserve_forwarded_https_redirect(request, response)
        assert response.headers["location"] == "https://ltclab.site/page"

    def test_308_external_host_not_rewritten(self) -> None:
        request = _mock_request({"x-forwarded-proto": "https"}, netloc="example.com")
        response = _mock_response(308, "http://other-site.com/page")
        _preserve_forwarded_https_redirect(request, response)
        # Different netloc, should NOT rewrite
        assert response.headers["location"] == "http://other-site.com/page"


class TestHostWithoutPort:
    def test_simple_host(self) -> None:
        request = _mock_request({"host": "example.com"})
        assert _host_without_port(request) == "example.com"

    def test_host_with_port(self) -> None:
        request = _mock_request({"host": "example.com:8080"})
        assert _host_without_port(request) == "example.com"

    def test_no_host_header(self) -> None:
        request = _mock_request()
        assert _host_without_port(request) == ""


class TestIsLtcLabHost:
    def test_ltclab_site(self) -> None:
        request = _mock_request({"host": "ltclab.site"})
        assert _is_ltc_lab_host(request) is True

    def test_www_ltclab_site(self) -> None:
        request = _mock_request({"host": "www.ltclab.site"})
        assert _is_ltc_lab_host(request) is True

    def test_not_ltc_lab(self) -> None:
        request = _mock_request({"host": "github.com"})
        assert _is_ltc_lab_host(request) is False

    def test_with_port(self) -> None:
        request = _mock_request({"host": "ltclab.site:443"})
        assert _is_ltc_lab_host(request) is True
