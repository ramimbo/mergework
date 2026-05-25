from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import create_schema, session_scope
from app.ledger.service import close_bounty, create_bounty, ensure_genesis, pay_bounty
from app.main import _signed_value, create_app
from app.models import BountyAttempt, LedgerEntry

COOKIE_SECRET = "test-cookie-secret"


def _set_login(client: TestClient, login: str) -> None:
    client.cookies.set("mrwk_user", _signed_value(login, COOKIE_SECRET))


def test_bounty_attempts_register_list_duplicate_and_release(sqlite_url: str, monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", COOKIE_SECRET)
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
            max_awards=2,
            acceptance="Register active attempts before opening overlapping PRs.",
        )
        ledger_height = session.scalar(
            select(LedgerEntry.sequence).order_by(LedgerEntry.sequence.desc())
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _set_login(client, "alice")

    created = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={
            "submitter_account": "GitHub:Alice",
            "source_url": "https://github.com/ramimbo/mergework/pull/500",
            "ttl_seconds": 3600,
        },
    )

    assert created.status_code == 201
    first_attempt = created.json()["attempt"]
    assert first_attempt["submitter_account"] == "github:alice"
    assert first_attempt["source_url"] == "https://github.com/ramimbo/mergework/pull/500"
    assert first_attempt["status"] == "active"
    assert created.json()["warnings"] == []

    duplicate = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={"submitter_account": "github:alice", "ttl_seconds": 3600},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["status"] == "duplicate_active_attempt"
    assert duplicate.json()["attempt"]["id"] == first_attempt["id"]

    spoofed = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={"submitter_account": "github:bob", "ttl_seconds": 3600},
    )
    assert spoofed.status_code == 403

    _set_login(client, "bob")
    second = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={"submitter_account": "github:bob", "ttl_seconds": 3600},
    )
    assert second.status_code == 201
    assert second.json()["warnings"] == ["bounty has 2 active attempts"]

    visible = client.get(f"/api/v1/bounties/{bounty.id}/attempts")
    assert visible.status_code == 200
    body = visible.json()
    assert body["warnings"] == ["bounty has 2 active attempts"]
    assert [attempt["submitter_account"] for attempt in body["attempts"]] == [
        "github:bob",
        "github:alice",
    ]

    wrong_submitter = client.post(
        f"/api/v1/bounty-attempts/{first_attempt['id']}/release",
        json={"submitter_account": "github:bob"},
    )
    assert wrong_submitter.status_code == 403

    _set_login(client, "alice")
    released = client.post(
        f"/api/v1/bounty-attempts/{first_attempt['id']}/release",
        json={"submitter_account": "github:alice"},
    )
    assert released.status_code == 200
    assert released.json()["status"] == "released"
    assert released.json()["attempt"]["status"] == "released"

    active_after_release = client.get(f"/api/v1/bounties/{bounty.id}/attempts").json()
    assert [attempt["submitter_account"] for attempt in active_after_release["attempts"]] == [
        "github:bob"
    ]

    all_attempts = client.get(f"/api/v1/bounties/{bounty.id}/attempts?include_expired=true").json()
    assert [attempt["status"] for attempt in all_attempts["attempts"]] == [
        "active",
        "released",
    ]

    with session_scope(sqlite_url) as session:
        assert (
            session.scalar(select(LedgerEntry.sequence).order_by(LedgerEntry.sequence.desc()))
            == ledger_height
        )


def test_expired_bounty_attempt_is_visible_but_no_longer_blocks_submitter(
    sqlite_url: str,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", COOKIE_SECRET)
    create_schema(sqlite_url)
    now = datetime.now(UTC)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=322,
            issue_url="https://github.com/ramimbo/mergework/issues/322",
            title="Expired attempt reservation",
            reward_mrwk="250",
            acceptance="Expired attempts should not block future contributors.",
        )
        session.add(
            BountyAttempt(
                bounty_id=bounty.id,
                submitter_account="github:alice",
                source_url="https://github.com/ramimbo/mergework/pull/501",
                status="active",
                expires_at=now - timedelta(minutes=5),
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
            )
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _set_login(client, "alice")

    active_only = client.get(f"/api/v1/bounties/{bounty.id}/attempts").json()
    assert active_only["attempts"] == []

    with_expired = client.get(f"/api/v1/bounties/{bounty.id}/attempts?include_expired=true").json()
    assert with_expired["attempts"][0]["status"] == "expired"

    replacement = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={"submitter_account": "github:alice", "ttl_seconds": 3600},
    )
    assert replacement.status_code == 201
    assert replacement.json()["attempt"]["status"] == "active"


def test_attempt_registration_rejects_closed_and_exhausted_bounties(
    sqlite_url: str, monkeypatch
) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", COOKIE_SECRET)
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        closed = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=323,
            issue_url="https://github.com/ramimbo/mergework/issues/323",
            title="Closed bounty",
            reward_mrwk="100",
            acceptance="Closed bounties should not accept new attempts.",
        )
        close_bounty(session, bounty_id=closed.id, closed_by="maintainer")
        paid = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=324,
            issue_url="https://github.com/ramimbo/mergework/issues/324",
            title="Paid bounty",
            reward_mrwk="100",
            acceptance="Paid bounties should not accept new attempts.",
        )
        pay_bounty(
            session,
            bounty_id=paid.id,
            to_account="github:winner",
            submission_url="https://github.com/ramimbo/mergework/pull/324",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _set_login(client, "alice")

    closed_response = client.post(
        f"/api/v1/bounties/{closed.id}/attempts",
        json={"submitter_account": "github:alice", "ttl_seconds": 3600},
    )
    assert closed_response.status_code == 409
    assert closed_response.json()["status"] == "not_available"
    assert closed_response.json()["warnings"] == [
        "bounty is closed",
        "bounty has no award slots remaining",
    ]

    paid_response = client.post(
        f"/api/v1/bounties/{paid.id}/attempts",
        json={"submitter_account": "github:alice", "ttl_seconds": 3600},
    )
    assert paid_response.status_code == 409
    assert paid_response.json()["status"] == "not_available"
    assert paid_response.json()["warnings"] == [
        "bounty is paid",
        "bounty has no award slots remaining",
    ]


def test_mcp_lists_bounty_attempts_for_agent_visibility(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    now = datetime.now(UTC)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="MCP attempt visibility",
            reward_mrwk="250",
            max_awards=3,
            acceptance="Agents should see active attempts before opening PRs.",
        )
        session.add_all(
            [
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:alice",
                    source_url="https://github.com/ramimbo/mergework/pull/501",
                    status="active",
                    expires_at=now + timedelta(hours=1),
                    created_at=now - timedelta(minutes=3),
                    updated_at=now - timedelta(minutes=3),
                ),
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:bob",
                    source_url="https://github.com/ramimbo/mergework/pull/502",
                    status="active",
                    expires_at=now + timedelta(hours=1),
                    created_at=now - timedelta(minutes=2),
                    updated_at=now - timedelta(minutes=2),
                ),
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:carol",
                    source_url=None,
                    status="released",
                    expires_at=now + timedelta(hours=1),
                    created_at=now - timedelta(minutes=4),
                    updated_at=now - timedelta(minutes=1),
                ),
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:dave",
                    source_url=None,
                    status="active",
                    expires_at=now - timedelta(minutes=1),
                    created_at=now - timedelta(minutes=5),
                    updated_at=now - timedelta(minutes=5),
                ),
            ]
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
    tool_names = {tool["name"] for tool in tools["result"]["tools"]}
    assert "list_bounty_attempts" in tool_names

    active_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "list_bounty_attempts",
                "arguments": {"bounty_id": bounty.id},
            },
        },
    ).json()

    active_payload = active_result["result"]["structuredContent"]
    assert active_payload["warnings"] == ["bounty has 2 active attempts"]
    assert [attempt["submitter_account"] for attempt in active_payload["attempts"]] == [
        "github:bob",
        "github:alice",
    ]
    assert json.loads(active_result["result"]["content"][0]["text"]) == active_payload

    all_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "list_bounty_attempts",
                "arguments": {"bounty_id": bounty.id, "include_expired": True},
            },
        },
    ).json()

    all_payload = all_result["result"]["structuredContent"]
    assert [attempt["status"] for attempt in all_payload["attempts"]] == [
        "active",
        "active",
        "released",
        "expired",
    ]

    unknown_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "list_bounty_attempts",
                "arguments": {"bounty_id": 999},
            },
        },
    ).json()
    assert unknown_result["result"]["content"][0]["text"] == "bounty not found"

    invalid_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "list_bounty_attempts",
                "arguments": {"bounty_id": bounty.id, "include_expired": "yes"},
            },
        },
    ).json()
    assert invalid_result["error"] == {"code": -32602, "message": "invalid tool arguments"}
