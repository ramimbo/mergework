from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis
from app.main import create_app
from app.mcp import MCP_TOOLS
from tests.mcp_conformance import (
    assert_mcp_tools_call_accepts,
    assert_mcp_tools_call_rejects,
    mcp_tools_by_name,
)


def _tools_with_input_schema() -> list[dict[str, Any]]:
    return [tool for tool in MCP_TOOLS if "inputSchema" in tool]


@pytest.mark.parametrize("tool", _tools_with_input_schema(), ids=lambda tool: tool["name"])
def test_mcp_tools_list_input_schema_disallows_extra_properties(tool: dict[str, Any]) -> None:
    schema = tool["inputSchema"]
    assert schema.get("type") == "object"
    assert schema.get("additionalProperties") is False


@pytest.mark.parametrize(
    ("arguments", "request_id"),
    [
        ({"format": "JSON"}, 101),
        ({"format": " JSON "}, 102),
        ({"format": "text "}, 103),
        ({"format": " json"}, 104),
        ({"format": None, "bounty_id": 1}, 105),
    ],
)
def test_mcp_submit_work_proof_rejects_non_exact_format_enum(
    sqlite_url: str, arguments: dict[str, object], request_id: int
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=794,
            issue_url="https://github.com/ramimbo/mergework/issues/794",
            title="MCP schema conformance",
            reward_mrwk="150",
            acceptance="Schema/runtime conformance guard.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    assert_mcp_tools_call_rejects(
        client,
        tool_name="submit_work_proof",
        arguments=arguments,
        request_id=request_id,
    )


@pytest.mark.parametrize(
    ("arguments", "request_id"),
    [
        ({"unexpected": "ignored"}, 110),
        ({"format": "json", "unexpected": "ignored"}, 111),
        ({"bounty_id": 1, "unexpected": "ignored"}, 112),
    ],
)
def test_mcp_submit_work_proof_conformance_rejects_undeclared_properties(
    sqlite_url: str, arguments: dict[str, object], request_id: int
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=794,
            issue_url="https://github.com/ramimbo/mergework/issues/794",
            title="MCP schema conformance",
            reward_mrwk="150",
            acceptance="Schema/runtime conformance guard.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    assert_mcp_tools_call_rejects(
        client,
        tool_name="submit_work_proof",
        arguments=arguments,
        request_id=request_id,
    )


@pytest.mark.parametrize(
    ("arguments", "request_id"),
    [
        ({"bounty_id": 1, "issue_number": 1}, 120),
        ({"repo": "ramimbo/mergework"}, 121),
        ({"bounty_id": 1, "repo": "ramimbo/mergework"}, 122),
        ({"issue_number": 1, "repo": 1}, 123),
    ],
)
def test_mcp_submit_work_proof_conformance_rejects_invalid_selectors(
    sqlite_url: str, arguments: dict[str, object], request_id: int
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=794,
            issue_url="https://github.com/ramimbo/mergework/issues/794",
            title="MCP schema conformance",
            reward_mrwk="150",
            acceptance="Schema/runtime conformance guard.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    assert_mcp_tools_call_rejects(
        client,
        tool_name="submit_work_proof",
        arguments=arguments,
        request_id=request_id,
    )


@pytest.mark.parametrize(
    ("arguments", "request_id"),
    [
        ({"bounty_id": "099"}, 130),
        ({"issue_number": "0656"}, 131),
        ({"bounty_id": "+1"}, 132),
    ],
)
def test_mcp_submit_work_proof_conformance_rejects_noncanonical_integers(
    sqlite_url: str, arguments: dict[str, object], request_id: int
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    assert_mcp_tools_call_rejects(
        client,
        tool_name="submit_work_proof",
        arguments=arguments,
        request_id=request_id,
    )


def test_mcp_submit_work_proof_conformance_accepts_schema_valid_examples(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=794,
            issue_url="https://github.com/ramimbo/mergework/issues/794",
            title="MCP schema conformance",
            reward_mrwk="150",
            acceptance="Schema/runtime conformance guard.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    tools = mcp_tools_by_name(client)
    submit_schema = tools["submit_work_proof"]["inputSchema"]
    assert submit_schema["properties"]["format"]["enum"] == ["text", "json"]

    assert_mcp_tools_call_accepts(
        client,
        tool_name="submit_work_proof",
        arguments={"format": "text"},
        request_id=140,
    )
    assert_mcp_tools_call_accepts(
        client,
        tool_name="submit_work_proof",
        arguments={"bounty_id": bounty.id, "format": "json"},
        request_id=141,
    )
    assert_mcp_tools_call_accepts(
        client,
        tool_name="submit_work_proof",
        arguments={"issue_number": 794, "format": "json"},
        request_id=142,
    )
    assert_mcp_tools_call_accepts(
        client,
        tool_name="submit_work_proof",
        arguments={
            "issue_number": 794,
            "repo": "ramimbo/mergework",
            "format": "text",
        },
        request_id=143,
    )
