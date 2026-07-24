from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from scripts.public_json_fetch import PublicJsonError, load_public_json


def test_load_public_json_returns_decoded_payload() -> None:
    payload = {"status": "ok"}
    response = io.BytesIO(json.dumps(payload).encode("utf-8"))
    response.status = 200  # type: ignore[attr-defined]

    with patch("urllib.request.urlopen", return_value=response):
        assert load_public_json("https://example.test/api/v1/status") == payload


def test_load_public_json_sets_default_headers() -> None:
    captured: dict[str, str] = {}

    class FakeResponse(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(b"[]")

    def fake_urlopen(request, timeout=30):  # noqa: ANN001
        captured["accept"] = request.get_header("Accept")
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = str(timeout)
        return FakeResponse()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        load_public_json("https://example.test/api/v1/bounties")

    assert captured["accept"] == "application/json"
    assert captured["user_agent"] == "mergework-maintenance-script"
    assert captured["timeout"] == "30"


def test_load_public_json_wraps_http_error() -> None:
    error = urllib.error.HTTPError(
        url="https://example.test/api/v1/bounties",
        code=503,
        msg="service unavailable",
        hdrs=None,
        fp=None,
    )
    with (
        patch("urllib.request.urlopen", side_effect=error),
        pytest.raises(PublicJsonError, match="MergeWork API bounty data unavailable: HTTP 503"),
    ):
        load_public_json(
            "https://example.test/api/v1/bounties",
            description="MergeWork API bounty data",
        )


def test_load_public_json_wraps_timeout() -> None:
    with (
        patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")),
        pytest.raises(PublicJsonError, match="public JSON unavailable:"),
    ):
        load_public_json("https://example.test/api/v1/status")


def test_load_public_json_wraps_invalid_json() -> None:
    response = io.BytesIO(b"not-json")

    with (
        patch("urllib.request.urlopen", return_value=response),
        pytest.raises(PublicJsonError, match="public JSON unavailable: invalid JSON"),
    ):
        load_public_json("https://example.test/api/v1/status")


def test_load_public_json_honors_custom_timeout() -> None:
    captured: dict[str, str] = {}

    class FakeResponse(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(b"{}")

    def fake_urlopen(request, timeout=30):  # noqa: ANN001
        captured["timeout"] = str(timeout)
        return FakeResponse()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        load_public_json("https://example.test/api/v1/status", timeout=12)

    assert captured["timeout"] == "12"
