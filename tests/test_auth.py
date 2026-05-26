from __future__ import annotations

import pytest
from fastapi import HTTPException, Request

from app.auth import AuthContext, safe_next_path, signed_value
from app.config import Settings


def _settings() -> Settings:
    return Settings(
        database_url="sqlite:///./mergework.sqlite3",
        public_base_url="https://mrwk.ltclab.site",
        github_webhook_secret="webhook-secret",
        github_oauth_client_id="client-id",
        github_oauth_client_secret="client-secret",
        admin_logins=("alice",),
        admin_token="admin-token",
        cookie_secret="cookie-secret",
        github_accepted_labelers=("alice",),
    )


def _request(*, cookie: str = "", headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    raw_headers = list(headers or [])
    if cookie:
        raw_headers.append((b"cookie", cookie.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw_headers})


def test_safe_next_path_keeps_only_internal_paths() -> None:
    assert safe_next_path("/wallets?tab=linked") == "/wallets?tab=linked"
    assert safe_next_path("https://evil.example/me") == "/me"
    assert safe_next_path("//evil.example/me") == "/me"
    assert safe_next_path("/admin\\evil") == "/me"


def test_auth_context_reads_signed_user_and_admin_cookies() -> None:
    auth = AuthContext(_settings())
    user_cookie = signed_value("ALICE", "cookie-secret")
    admin_cookie = signed_value("alice", "cookie-secret")
    request = _request(cookie=f"mrwk_user={user_cookie}; mrwk_admin={admin_cookie}")

    assert auth.github_login_from_request(request) == "alice"
    assert auth.admin_login_from_request(request) == "alice"
    assert auth.require_github_login(request) == "alice"
    assert auth.require_admin(request) == "alice"


def test_auth_context_keeps_admin_token_separate_from_cookie_admin() -> None:
    auth = AuthContext(_settings())
    token_request = _request(headers=[(b"x-mergework-admin-token", b"admin-token")])
    user_cookie = signed_value("bob", "cookie-secret")
    non_admin_cookie_request = _request(cookie=f"mrwk_admin={user_cookie}")

    assert auth.require_admin_token(token_request) == "api-token"
    assert auth.admin_login_from_request(token_request) == "api-token"
    assert auth.admin_login_from_request(non_admin_cookie_request) is None
    with pytest.raises(HTTPException) as exc_info:
        auth.require_admin(non_admin_cookie_request)
    assert exc_info.value.status_code == 401


def test_auth_context_verifies_oauth_state_and_next_path() -> None:
    auth = AuthContext(_settings())
    valid_state = auth.signed_value("nonce,/wallets")
    external_next_state = auth.signed_value("nonce,https://evil.example")
    malformed_state = auth.signed_value("missing-comma")

    assert auth.verify_oauth_state(valid_state, valid_state) == "/wallets"
    assert auth.verify_oauth_state(external_next_state, external_next_state) == "/me"
    with pytest.raises(HTTPException) as mismatch:
        auth.verify_oauth_state(valid_state, auth.signed_value("other,/wallets"))
    assert mismatch.value.status_code == 401
    assert mismatch.value.detail == "invalid OAuth state"
    with pytest.raises(HTTPException) as malformed:
        auth.verify_oauth_state(malformed_state, malformed_state)
    assert malformed.value.status_code == 401
    assert malformed.value.detail == "invalid OAuth state"


def test_csrf_uses_same_signed_cookie_boundary() -> None:
    auth = AuthContext(_settings())
    token = auth.csrf_token("admin-bounty", "alice")

    assert auth.verify_csrf_token(token, action="admin-bounty", login="alice")
    assert not auth.verify_csrf_token(token, action="admin-bounty", login="bob")
