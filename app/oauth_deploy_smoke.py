from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EXPRESS_CANNOT_GET = "Cannot GET"
OAUTH_ROUTE_PATHS = (
    "/auth/github/login",
    "/auth/github/callback",
)


def _probe_database_url(database_url: str | None) -> tuple[str, str | None, str | None]:
    if database_url is not None:
        return database_url, None, None
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    return f"sqlite:///{path}", path, None


def validate_oauth_routes_registered(database_url: str | None = None) -> list[str]:
    """Ensure GitHub OAuth browser routes are registered in the running app."""
    probe_url, temp_path, prior_db = _probe_database_url(database_url)
    patch_env = "app.main" not in sys.modules and database_url is None
    if patch_env:
        prior_db = os.environ.get("MERGEWORK_DATABASE_URL")
        os.environ["MERGEWORK_DATABASE_URL"] = probe_url

    try:
        from fastapi.testclient import TestClient

        from app.main import create_app

        client = TestClient(create_app(database_url=probe_url, webhook_secret="deploy-smoke"))
        errors: list[str] = []
        for path in OAUTH_ROUTE_PATHS:
            response = client.get(path, follow_redirects=False)
            if response.status_code == 404:
                errors.append(f"{path} returned 404 — OAuth route is not registered")
            elif EXPRESS_CANNOT_GET in response.text:
                errors.append(f"{path} returned an Express Cannot GET shell")

        login = client.get("/auth/github/login", follow_redirects=False)
        if login.status_code not in {503, 302}:
            errors.append(
                "GET /auth/github/login should return 503 when OAuth is unconfigured "
                f"or 302 when configured; got {login.status_code}"
            )

        callback = client.get("/auth/github/callback", follow_redirects=False)
        if callback.status_code != 422:
            errors.append(
                "GET /auth/github/callback without query params should return 422 "
                f"when registered; got {callback.status_code}"
            )

        return errors
    finally:
        if patch_env:
            if prior_db is None:
                os.environ.pop("MERGEWORK_DATABASE_URL", None)
            else:
                os.environ["MERGEWORK_DATABASE_URL"] = prior_db
        if temp_path is not None:
            os.unlink(temp_path)


def is_healthy_oauth_route(status_code: int | None, body: str) -> bool:
    if status_code is None:
        return False
    if EXPRESS_CANNOT_GET in body:
        return False
    if status_code == 404:
        return False
    return status_code in {200, 302, 422, 503}


def oauth_route_paths() -> tuple[str, ...]:
    return OAUTH_ROUTE_PATHS
