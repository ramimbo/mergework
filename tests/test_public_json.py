from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from scripts.public_json import (
    DEFAULT_TIMEOUT_SECONDS,
    JSON_ACCEPT_HEADERS,
    fetch_public_json,
)


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_fetch_public_json_builds_request_with_headers_and_decodes() -> None:
    seen: dict[str, Any] = {}

    def fake_opener(target: Any, *, timeout: float) -> _FakeResponse:
        seen["full_url"] = target.full_url
        seen["accept"] = target.get_header("Accept")
        seen["timeout"] = timeout
        return _FakeResponse({"ok": True})

    result = fetch_public_json(
        "https://api.example.test/data",
        headers=JSON_ACCEPT_HEADERS,
        opener=fake_opener,
    )

    assert result == {"ok": True}
    assert seen["full_url"] == "https://api.example.test/data"
    assert seen["accept"] == "application/json"
    assert seen["timeout"] == DEFAULT_TIMEOUT_SECONDS


def test_fetch_public_json_passes_raw_url_when_build_request_false() -> None:
    seen: dict[str, Any] = {}

    def fake_opener(target: Any, *, timeout: float) -> _FakeResponse:
        seen["target"] = target
        seen["timeout"] = timeout
        return _FakeResponse([1, 2, 3])

    result = fetch_public_json(
        "https://api.example.test/raw",
        timeout=12,
        opener=fake_opener,
        build_request=False,
    )

    assert result == [1, 2, 3]
    assert seen["target"] == "https://api.example.test/raw"
    assert seen["timeout"] == 12


def test_fetch_public_json_propagates_network_errors_unwrapped() -> None:
    def fake_opener(target: Any, *, timeout: float) -> _FakeResponse:
        raise urllib.error.URLError("offline")

    with pytest.raises(urllib.error.URLError):
        fetch_public_json("https://api.example.test/down", opener=fake_opener)


class _BadResponse:
    def __enter__(self) -> _BadResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b"not-json"


def test_fetch_public_json_raises_on_invalid_json() -> None:
    def fake_opener(target: Any, *, timeout: float) -> _BadResponse:
        return _BadResponse()

    with pytest.raises(json.JSONDecodeError):
        fetch_public_json("https://api.example.test/bad", opener=fake_opener)
