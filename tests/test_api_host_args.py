from __future__ import annotations

import argparse
import json
import urllib.error

import pytest

from scripts import api_host_args
from scripts.api_host_args import load_public_json, public_api_host, public_http_url


def test_public_http_url_rejects_blank_and_relative_values() -> None:
    with pytest.raises(ValueError, match=r"service URL must be a non-empty HTTP\(S\) URL"):
        public_http_url("   ", label="service URL")
    with pytest.raises(ValueError, match=r"service URL must be an absolute HTTP\(S\) URL"):
        public_http_url("/api/v1/bounties", label="service URL")


def test_public_http_url_can_reject_embedded_credentials() -> None:
    with pytest.raises(ValueError, match="service URL must not include username or password"):
        public_http_url(
            "https://operator:secret@staging.mrwk.example.test",
            label="service URL",
            forbid_credentials=True,
        )

    assert public_http_url("https://staging.mrwk.example.test", label="service URL") == (
        "https://staging.mrwk.example.test"
    )


def test_public_api_host_preserves_argparse_error_type() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="api host must be an absolute"):
        public_api_host("localhost:8000")


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_load_public_json_sets_json_accept_header_and_optional_user_agent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, *, timeout):
        captured["accept"] = request.headers.get("Accept")
        captured["user_agent"] = request.headers.get("User-agent")
        captured["timeout"] = timeout
        return _FakeResponse(json.dumps({"ok": True}).encode("utf-8"))

    monkeypatch.setattr(api_host_args.urllib.request, "urlopen", fake_urlopen)

    payload = load_public_json(
        "https://api.example.test/status",
        timeout_seconds=12,
        user_agent="mergework-tests",
    )

    assert payload == {"ok": True}
    assert captured == {
        "accept": "application/json",
        "user_agent": "mergework-tests",
        "timeout": 12,
    }


def test_load_public_json_raises_json_decode_error_for_malformed_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        api_host_args.urllib.request,
        "urlopen",
        lambda request, *, timeout: _FakeResponse(b"{not-json"),
    )

    with pytest.raises(json.JSONDecodeError):
        load_public_json("https://api.example.test/status", timeout_seconds=5)


def test_load_public_json_propagates_network_failures(monkeypatch) -> None:
    def fake_urlopen(request, *, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(api_host_args.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(urllib.error.URLError):
        load_public_json("https://api.example.test/status", timeout_seconds=5)
