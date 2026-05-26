from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response

from app.config import Settings

USER_COOKIE_NAME = "mrwk_user"
ADMIN_COOKIE_NAME = "mrwk_admin"
OAUTH_STATE_COOKIE_NAME = "mrwk_oauth_state"
USER_COOKIE_MAX_AGE_SECONDS = 604_800
ADMIN_COOKIE_MAX_AGE_SECONDS = 86_400
OAUTH_STATE_MAX_AGE_SECONDS = 600
CSRF_MAX_AGE_SECONDS = 3_600


def oauth_configured(settings: Settings) -> bool:
    return bool(
        settings.github_oauth_client_id
        and settings.github_oauth_client_secret
        and settings.cookie_secret
    )


def safe_next_path(next_path: str | None) -> str:
    if (
        not next_path
        or not next_path.startswith("/")
        or next_path.startswith("//")
        or len(next_path) > 2048
        or "\\" in next_path
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in next_path)
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


@dataclass(frozen=True)
class AuthContext:
    settings: Settings

    def oauth_configured(self) -> bool:
        return oauth_configured(self.settings)

    def signed_value(self, value: str) -> str:
        return signed_value(value, self.settings.cookie_secret)

    def github_login_from_request(self, request: Request) -> str | None:
        login = verified_value(
            request.cookies.get(USER_COOKIE_NAME),
            self.settings.cookie_secret,
            USER_COOKIE_MAX_AGE_SECONDS,
        )
        return login.lower() if login else None

    def admin_login_from_request(self, request: Request) -> str | None:
        token = request.headers.get("x-mergework-admin-token", "")
        if self.settings.admin_token and hmac.compare_digest(token, self.settings.admin_token):
            return "api-token"
        login = verified_value(
            request.cookies.get(ADMIN_COOKIE_NAME),
            self.settings.cookie_secret,
            ADMIN_COOKIE_MAX_AGE_SECONDS,
        )
        if login and login.lower() in self.settings.admin_logins:
            return login.lower()
        return None

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
        if self.settings.admin_token and hmac.compare_digest(token, self.settings.admin_token):
            return "api-token"
        raise HTTPException(status_code=401, detail="admin token required")

    def csrf_token(self, action: str, login: str) -> str:
        return csrf_token(action, login, self.settings.cookie_secret)

    def verify_csrf_token(self, token: str | None, *, action: str, login: str) -> bool:
        return verify_csrf_token(
            token,
            action=action,
            login=login,
            secret=self.settings.cookie_secret,
        )

    def verify_oauth_state(self, cookie_state: str | None, state: str) -> str:
        if not cookie_state or not hmac.compare_digest(cookie_state, state):
            raise HTTPException(status_code=401, detail="invalid OAuth state")
        state_value = verified_value(
            state, self.settings.cookie_secret, OAUTH_STATE_MAX_AGE_SECONDS
        )
        if state_value is None:
            raise HTTPException(status_code=401, detail="expired OAuth state")
        try:
            _, next_path = state_value.split(",", 1)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="invalid OAuth state") from exc
        return safe_next_path(next_path)

    def set_oauth_state_cookie(self, response: Response, state: str) -> None:
        response.set_cookie(
            OAUTH_STATE_COOKIE_NAME,
            state,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=OAUTH_STATE_MAX_AGE_SECONDS,
        )

    def set_login_cookies(self, response: Response, login: str) -> None:
        response.set_cookie(
            USER_COOKIE_NAME,
            self.signed_value(login),
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=USER_COOKIE_MAX_AGE_SECONDS,
        )
        if login in self.settings.admin_logins:
            response.set_cookie(
                ADMIN_COOKIE_NAME,
                self.signed_value(login),
                httponly=True,
                secure=True,
                samesite="lax",
                max_age=ADMIN_COOKIE_MAX_AGE_SECONDS,
            )

    def clear_session_cookies(self, response: Response) -> None:
        response.delete_cookie(USER_COOKIE_NAME)
        response.delete_cookie(ADMIN_COOKIE_NAME)
