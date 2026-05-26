from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.webhooks.github import handle_github_webhook

GITHUB_WEBHOOK_HEADERS = {
    "X-GitHub-Delivery": "x-github-delivery",
    "X-GitHub-Event": "x-github-event",
    "X-Hub-Signature-256": "x-hub-signature-256",
}


def github_webhook_headers(headers: Mapping[str, str]) -> dict[str, str]:
    normalized = {key.lower(): value for key, value in headers.items()}
    return {
        canonical: normalized.get(header_name, "")
        for canonical, header_name in GITHUB_WEBHOOK_HEADERS.items()
    }


def github_webhook_status_code(result: Mapping[str, Any]) -> int:
    return 401 if result.get("status") == "unauthorized" else 200


def register_github_webhook_route(
    app: FastAPI,
    *,
    database_url: str,
    webhook_secret: str,
    accepted_labelers: tuple[str, ...] = (),
) -> None:
    @app.post("/webhooks/github")
    async def github_webhook(request: Request) -> JSONResponse:
        body = await request.body()
        result = handle_github_webhook(
            database_url,
            github_webhook_headers(request.headers),
            body,
            webhook_secret,
            accepted_labelers,
        )
        return JSONResponse(result, status_code=github_webhook_status_code(result))
