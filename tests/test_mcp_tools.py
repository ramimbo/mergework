from __future__ import annotations

import json

import pytest

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis
from app.mcp_tools import call_mcp_tool


def test_call_mcp_tool_lists_bounties_without_app_route(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="MCP dispatch extraction",
            reward_mrwk="200",
            acceptance="MCP tools can be tested outside app route wiring.",
        )

    result = call_mcp_tool(sqlite_url, "list_bounties", {"q": "dispatch"})

    assert isinstance(result, str)
    bounties = json.loads(result)
    assert bounties[0]["title"] == "MCP dispatch extraction"
    assert bounties[0]["issue_number"] == 320


def test_call_mcp_tool_rejects_invalid_arguments_without_http_layer(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    with pytest.raises(ValueError, match="sequence must be positive"):
        call_mcp_tool(sqlite_url, "get_ledger_entry", {"sequence": 0})
