from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.auth import safe_next_path, signed_value, verified_value
from app.main import create_app


def test_signed_value_round_trips_and_rejects_wrong_secret() -> None:
    token = signed_value("github:alice", "test-cookie-secret")

    assert verified_value(token, "test-cookie-secret", 60) == "github:alice"
    assert verified_value(token, "wrong-secret", 60) is None


def test_signed_value_rejects_expired_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.auth.time.time", lambda: 1_000_000)
    token = signed_value("github:alice", "test-cookie-secret")

    assert verified_value(token, "test-cookie-secret", 60) == "github:alice"

    monkeypatch.setattr("app.auth.time.time", lambda: 1_000_061)
    assert verified_value(token, "test-cookie-secret", 60) is None


@pytest.mark.parametrize(
    ("next_path", "expected"),
    [
        ("/me", "/me"),
        ("/bounties?status=open", "/bounties?status=open"),
        ("/%2f%2fevil.example/me", "/me"),
        ("/%5cevil.example/me", "/me"),
        ("/me%0d%0aLocation:%20https://evil.example", "/me"),
    ],
)
def test_safe_next_path_rejects_encoded_redirect_ambiguity(next_path: str, expected: str) -> None:
    assert safe_next_path(next_path) == expected


def test_auth_login_route_uses_safe_next_path(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    monkeypatch.setenv("MERGEWORK_PUBLIC_BASE_URL", "https://mrwk.example.test")
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(
        "/auth/github/login?next=/%2f%2fevil.example/path", follow_redirects=False
    )

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    state_value = verified_value(query["state"][0], "test-cookie-secret", 600)
    assert state_value is not None
    _nonce, next_path = state_value.split(",", 1)
    assert next_path == "/me"
