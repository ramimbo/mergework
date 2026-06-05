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
from app.control_chars import contains_control_character


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
        or len(decoded_next_path) > 2048
        or "\\" in decoded_next_path
        or contains_control_character(next_path)
        or contains_control_character(decoded_next_path)
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


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def admin_login_from_request(self, request: Request) -> str | None:
        token = request.headers.get("x-mergework-admin-token", "")
        if self._settings.admin_token and hmac.compare_digest(token, self._settings.admin_token):
            return "api-token"
        login = verified_value(
            request.cookies.get("mrwk_admin"), self._settings.cookie_secret, 86_400
        )
        if login and login.lower() in self._settings.admin_logins:
            return login.lower()
        return None

    def github_login_from_request(self, request: Request) -> str | None:
        login = verified_value(
            request.cookies.get("mrwk_user"), self._settings.cookie_secret, 604_800
        )
        return login.lower() if login else None

    def require_github_login(self, request: Request) -> str:
        login = self.github_login_from_request(request)
        if login is None:
            raise HTTPException(status_code=401, detail="github login required")
        return login

    def require_admin(self, request: Request) -> str:
        login = self.admin_login_from_request(request)
        if login is None:
            raise HTTPException(status_code=401, detail="admin authentication required")
        return login

    def require_admin_token(self, request: Request) -> str:
        token = request.headers.get("x-mergework-admin-token", "")
        if self._settings.admin_token and hmac.compare_digest(token, self._settings.admin_token):
            return "api-token"
        raise HTTPException(status_code=401, detail="admin token required")


def register_auth_routes(app: FastAPI, *, settings: Settings) -> AuthService:
    auth = AuthService(settings)

    @app.get("/auth/github/login")
    def auth_github_login(next_path: str | None = Query(None, alias="next")) -> RedirectResponse:
        if not oauth_configured(settings):
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        safe_next = safe_next_path(next_path)
        state_value = f"{secrets.token_urlsafe(24)},{safe_next}"
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
            "mrwk_oauth_state", state, httponly=True, secure=True, samesite="lax", max_age=600
        )
        return response

    @app.get("/auth/github/callback")
    async def auth_github_callback(request: Request, code: str, state: str) -> RedirectResponse:
        if not oauth_configured(settings):
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        cookie_state = request.cookies.get("mrwk_oauth_state")
        if not cookie_state or not hmac.compare_digest(cookie_state, state):
            raise HTTPException(status_code=401, detail="invalid OAuth state")
        state_value = verified_value(state, settings.cookie_secret, 600)
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
        response = RedirectResponse(next_path, status_code=302)
        response.set_cookie(
            "mrwk_user",
            signed_value(login, settings.cookie_secret),
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=604_800,
        )
        if login in settings.admin_logins:
            response.set_cookie(
                "mrwk_admin",
                signed_value(login, settings.cookie_secret),
                httponly=True,
                secure=True,
                samesite="lax",
                max_age=86_400,
            )
        response.delete_cookie("mrwk_oauth_state")
        return response

    @app.post("/auth/logout")
    def auth_logout() -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("mrwk_user")
        response.delete_cookie("mrwk_admin")
        return response

    return auth
