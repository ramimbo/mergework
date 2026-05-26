from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from urllib.parse import unquote, urlencode

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.config import Settings

OAUTH_STATE_COOKIE = "mrwk_oauth_state"
USER_COOKIE = "mrwk_user"
ADMIN_COOKIE = "mrwk_admin"
OAUTH_STATE_MAX_AGE_SECONDS = 600
USER_SESSION_MAX_AGE_SECONDS = 604_800
ADMIN_SESSION_MAX_AGE_SECONDS = 86_400
CSRF_MAX_AGE_SECONDS = 3_600


def oauth_configured(settings: Settings) -> bool:
    return bool(
        settings.github_oauth_client_id
        and settings.github_oauth_client_secret
        and settings.cookie_secret
    )


def safe_next_path(next_path: str | None) -> str:
    decoded_next_path = unquote(next_path) if next_path else ""
    if (
        not next_path
        or not next_path.startswith("/")
        or next_path.startswith("//")
        or len(next_path) > 2048
        or "\\" in next_path
        or decoded_next_path.startswith("//")
        or "\\" in decoded_next_path
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in next_path)
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in decoded_next_path)
    ):
        return "/me"
    return next_path


def signed_value(value: str, secret: str) -> str:
    timestamp = str(int(time.time()))
    body = f"{value}|{timestamp}"
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}|{signature}"


def verified_value(token: str | None, secret: str, max_age_seconds: int) -> str | None:
    if not token or not secret:
        return None
    try:
        value, timestamp, signature = token.rsplit("|", 2)
        age = int(time.time()) - int(timestamp)
    except ValueError:
        return None
    if age < 0 or age > max_age_seconds:
        return None
    expected = hmac.new(
        secret.encode(), f"{value}|{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return value


def csrf_token(action: str, login: str, secret: str) -> str:
    return signed_value(f"{action}:{login}", secret)


def verify_csrf_token(
    token: str | None,
    *,
    action: str,
    login: str,
    secret: str,
    max_age_seconds: int = CSRF_MAX_AGE_SECONDS,
) -> bool:
    expected = f"{action}:{login}"
    return verified_value(token, secret, max_age_seconds) == expected


def admin_login_from_request(request: Request, settings: Settings) -> str | None:
    token = request.headers.get("x-mergework-admin-token", "")
    if settings.admin_token and hmac.compare_digest(token, settings.admin_token):
        return "api-token"
    login = verified_value(
        request.cookies.get(ADMIN_COOKIE), settings.cookie_secret, ADMIN_SESSION_MAX_AGE_SECONDS
    )
    if login and login.lower() in settings.admin_logins:
        return login.lower()
    return None


def github_login_from_request(request: Request, settings: Settings) -> str | None:
    login = verified_value(
        request.cookies.get(USER_COOKIE), settings.cookie_secret, USER_SESSION_MAX_AGE_SECONDS
    )
    return login.lower() if login else None


def require_admin_token_from_request(request: Request, settings: Settings) -> str:
    token = request.headers.get("x-mergework-admin-token", "")
    if settings.admin_token and hmac.compare_digest(token, settings.admin_token):
        return "api-token"
    raise HTTPException(status_code=401, detail="admin token required")


async def _github_oauth_login_from_callback(
    request: Request, settings: Settings, code: str, state: str
) -> tuple[str, str]:
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not cookie_state or not hmac.compare_digest(cookie_state, state):
        raise HTTPException(status_code=401, detail="invalid OAuth state")
    state_value = verified_value(state, settings.cookie_secret, OAUTH_STATE_MAX_AGE_SECONDS)
    if state_value is None:
        raise HTTPException(status_code=401, detail="expired OAuth state")
    try:
        _, next_path = state_value.split(",", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid OAuth state") from exc
    next_path = safe_next_path(next_path)
    async with httpx.AsyncClient(timeout=10) as client:
        token_response = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_oauth_client_id,
                "client_secret": settings.github_oauth_client_secret,
                "code": code,
                "redirect_uri": f"{settings.public_base_url}/auth/github/callback",
            },
        )
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise HTTPException(status_code=401, detail="GitHub OAuth token exchange failed")
        user_response = await client.get(
            "https://api.github.com/user",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
            },
        )
        user_response.raise_for_status()
        login = str(user_response.json().get("login", "")).lower()
        if not login:
            raise HTTPException(status_code=401, detail="GitHub OAuth user lookup failed")
    return login, next_path


def _set_github_session_cookies(
    response: RedirectResponse, login: str, settings: Settings
) -> RedirectResponse:
    response.set_cookie(
        USER_COOKIE,
        signed_value(login, settings.cookie_secret),
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=USER_SESSION_MAX_AGE_SECONDS,
    )
    if login in settings.admin_logins:
        response.set_cookie(
            ADMIN_COOKIE,
            signed_value(login, settings.cookie_secret),
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=ADMIN_SESSION_MAX_AGE_SECONDS,
        )
    return response


def register_auth_routes(app: FastAPI, settings: Settings) -> None:
    @app.get("/auth/github/login")
    def auth_github_login(next_path: str | None = Query(None, alias="next")) -> RedirectResponse:
        if not oauth_configured(settings):
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        state_value = f"{secrets.token_urlsafe(24)},{safe_next_path(next_path)}"
        state = signed_value(state_value, settings.cookie_secret)
        query = urlencode(
            {
                "client_id": settings.github_oauth_client_id,
                "redirect_uri": f"{settings.public_base_url}/auth/github/callback",
                "scope": "read:user",
                "state": state,
            }
        )
        response = RedirectResponse(
            f"https://github.com/login/oauth/authorize?{query}", status_code=302
        )
        response.set_cookie(
            OAUTH_STATE_COOKIE,
            state,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=OAUTH_STATE_MAX_AGE_SECONDS,
        )
        return response

    @app.get("/auth/github/callback")
    async def auth_github_callback(request: Request, code: str, state: str) -> RedirectResponse:
        if not oauth_configured(settings):
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        login, next_path = await _github_oauth_login_from_callback(request, settings, code, state)
        response = _set_github_session_cookies(
            RedirectResponse(next_path, status_code=302), login, settings
        )
        response.delete_cookie(OAUTH_STATE_COOKIE)
        return response

    @app.get("/admin/login")
    def admin_login() -> RedirectResponse:
        return RedirectResponse("/auth/github/login?next=/admin", status_code=302)

    @app.get("/admin/callback")
    async def admin_callback(request: Request) -> RedirectResponse:
        suffix = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/auth/github/callback{suffix}", status_code=302)

    @app.post("/auth/logout")
    def auth_logout() -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(USER_COOKIE)
        response.delete_cookie(ADMIN_COOKIE)
        return response
