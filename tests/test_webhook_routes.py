from __future__ import annotations

import hashlib
import hmac

from fastapi.testclient import TestClient

from app.db import session_scope
from app.main import create_app
from app.models import WebhookEvent


def _signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_github_webhook_route_normalizes_headers(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    body = b'{"action":"opened"}'

    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Delivery": "delivery-route-ignored",
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": _signature("secret", body),
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    with session_scope(sqlite_url) as session:
        event = session.get(WebhookEvent, "delivery-route-ignored")
        assert event is not None
        assert event.event_type == "issues"
        assert event.processed_status == "ignored"


def test_github_webhook_route_returns_401_for_bad_signature(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/webhooks/github",
        content=b'{"action":"opened"}',
        headers={
            "X-GitHub-Delivery": "delivery-bad-signature",
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": "sha256=bad",
        },
    )

    assert response.status_code == 401
    assert response.json() == {"status": "unauthorized"}
