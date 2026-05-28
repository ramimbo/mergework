from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis
from app.main import create_app
from app.mcp_tools import call_mcp_tool


def test_api_bounties_offset_pagination(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        for i in range(5):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=100 + i,
                issue_url=f"https://github.com/ramimbo/mergework/issues/{100 + i}",
                title=f"Bounty {i}",
                reward_mrwk="10",
                acceptance="Test acceptance.",
            )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    all_rows = client.get("/api/v1/bounties").json()
    assert len(all_rows) == 5

    limited = client.get("/api/v1/bounties?limit=2").json()
    assert len(limited) == 2
    assert limited == all_rows[:2]

    offset = client.get("/api/v1/bounties?limit=2&offset=2").json()
    assert len(offset) == 2
    assert offset == all_rows[2:4]

    offset_end = client.get("/api/v1/bounties?limit=2&offset=4").json()
    assert len(offset_end) == 1
    assert offset_end == all_rows[4:]

    offset_overshoot = client.get("/api/v1/bounties?limit=2&offset=10").json()
    assert offset_overshoot == []

    offset_zero = client.get("/api/v1/bounties?limit=2&offset=0").json()
    assert offset_zero == all_rows[:2]


def test_api_bounties_summary_respects_offset(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        for i in range(3):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=200 + i,
                issue_url=f"https://github.com/ramimbo/mergework/issues/{200 + i}",
                title=f"Summary bounty {i}",
                reward_mrwk="20",
                acceptance="Test acceptance.",
            )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    full = client.get("/api/v1/bounties/summary").json()
    assert full["bounties_shown"] == 3

    limited = client.get("/api/v1/bounties/summary?limit=1&offset=1").json()
    assert limited["bounties_shown"] == 1


def test_public_bounties_page_offset(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        for i in range(3):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=300 + i,
                issue_url=f"https://github.com/ramimbo/mergework/issues/{300 + i}",
                title=f"Public bounty {i}",
                reward_mrwk="15",
                acceptance="Test acceptance.",
            )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    page = client.get("/bounties?offset=1")
    assert page.status_code == 200
    assert "Public bounty 2" not in page.text
    assert "Public bounty 1" in page.text
    assert "Public bounty 0" in page.text

    page_limit = client.get("/bounties?offset=1&limit=1")
    assert page_limit.status_code == 200
    assert "Public bounty 2" not in page_limit.text
    assert "Public bounty 1" in page_limit.text
    assert "Public bounty 0" not in page_limit.text


def test_mcp_list_bounties_offset(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        for i in range(4):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=400 + i,
                issue_url=f"https://github.com/ramimbo/mergework/issues/{400 + i}",
                title=f"MCP bounty {i}",
                reward_mrwk="5",
                acceptance="Test acceptance.",
            )

    result = call_mcp_tool(sqlite_url, "list_bounties", {"status": "open", "limit": 2, "offset": 1})
    bounties = json.loads(result)
    assert len(bounties) == 2
    assert bounties[0]["title"] == "MCP bounty 2"


def test_api_bounties_rejects_negative_offset(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    response = client.get("/api/v1/bounties?offset=-1")
    assert response.status_code == 422
