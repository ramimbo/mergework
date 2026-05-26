from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis
from app.main import create_app
from app.models import BountyAttempt


def test_mcp_list_bounty_attempts_reports_active_and_expired(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    now = datetime.now(UTC)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Attempt reservations",
            reward_mrwk="250",
            max_awards=2,
            acceptance="Agents should inspect active attempts before opening work.",
        )
        session.add_all(
            [
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:alice",
                    source_url="https://github.com/ramimbo/mergework/pull/501",
                    status="active",
                    expires_at=now + timedelta(hours=1),
                    created_at=now - timedelta(minutes=2),
                    updated_at=now - timedelta(minutes=2),
                ),
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:bob",
                    source_url="https://github.com/ramimbo/mergework/pull/502",
                    status="active",
                    expires_at=now + timedelta(hours=2),
                    created_at=now - timedelta(minutes=1),
                    updated_at=now - timedelta(minutes=1),
                ),
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:carol",
                    source_url="https://github.com/ramimbo/mergework/pull/503",
                    status="active",
                    expires_at=now - timedelta(minutes=1),
                    created_at=now - timedelta(minutes=3),
                    updated_at=now - timedelta(minutes=3),
                ),
            ]
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    active_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "name": "list_bounty_attempts",
                "arguments": {"bounty_id": bounty.id},
            },
        },
    ).json()["result"]

    active_payload = active_result["structuredContent"]
    assert active_payload["bounty_id"] == bounty.id
    assert active_payload["issue_number"] == 321
    assert active_payload["warnings"] == ["bounty has 2 active attempts"]
    assert [attempt["submitter_account"] for attempt in active_payload["attempts"]] == [
        "github:bob",
        "github:alice",
    ]

    all_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 22,
            "method": "tools/call",
            "params": {
                "name": "list_bounty_attempts",
                "arguments": {"bounty_id": bounty.id, "include_expired": True, "limit": 3},
            },
        },
    ).json()["result"]

    all_payload = all_result["structuredContent"]
    assert [attempt["status"] for attempt in all_payload["attempts"]] == [
        "active",
        "active",
        "expired",
    ]


def test_mcp_list_bounty_attempts_rejects_invalid_arguments(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Attempt reservations",
            reward_mrwk="250",
            acceptance="Agents should inspect attempts before opening work.",
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 23,
            "method": "tools/call",
            "params": {
                "name": "list_bounty_attempts",
                "arguments": {"bounty_id": bounty_id, "include_expired": "yes"},
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 23,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }
