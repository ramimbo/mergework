from __future__ import annotations

import json

import pytest

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis
from app.mcp_tools import call_mcp_tool


def test_call_mcp_tool_lists_bounties_from_extracted_dispatcher(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=390,
            issue_url="https://github.com/ramimbo/mergework/issues/390",
            title="Code health bounty",
            reward_mrwk="200",
            acceptance="Extract a coherent subsystem from app.main.",
        )

    result = call_mcp_tool(sqlite_url, "list_bounties", {"status": "open"})

    bounties = json.loads(result)
    assert bounties[0]["issue_number"] == 390
    assert bounties[0]["title"] == "Code health bounty"


def test_submit_work_proof_repo_selector_matches_stored_repo_case(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=390,
            issue_url="https://github.com/ramimbo/mergework/issues/390",
            title="Code health bounty",
            reward_mrwk="200",
            acceptance="Extract a coherent subsystem from app.main.",
        )
        bounty.repo = "Ramimbo/MergeWork"

    result = call_mcp_tool(
        sqlite_url,
        "submit_work_proof",
        {"issue_number": 390, "repo": "ramimbo/mergework"},
    )

    assert "Code health bounty" in result
    assert "Bounty #390" in result


@pytest.mark.parametrize(
    ("tool_name", "arguments", "message"),
    [
        ("list_bounties", {"status": "blocked"}, "status must be one of"),
        ("get_bounty", {"id": 0}, "id must be positive"),
        ("get_balance", {"account": ""}, "account must not be empty"),
    ],
)
def test_call_mcp_tool_preserves_argument_validation_errors(
    sqlite_url: str, tool_name: str, arguments: dict[str, object], message: str
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    with pytest.raises(ValueError, match=message):
        call_mcp_tool(sqlite_url, tool_name, arguments)


@pytest.mark.parametrize(
    ("tool_name", "arguments", "message"),
    [
        ("get_bounty", {"id": "\t1"}, "id must not contain control characters"),
        (
            "list_bounty_attempts",
            {"bounty_id": "1\n"},
            "bounty_id must not contain control characters",
        ),
        (
            "submit_work_proof",
            {"issue_number": "\x7f390"},
            "issue_number must not contain control characters",
        ),
    ],
)
def test_mcp_integer_arguments_reject_control_characters_before_trimming(
    sqlite_url: str, tool_name: str, arguments: dict[str, object], message: str
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=390,
            issue_url="https://github.com/ramimbo/mergework/issues/390",
            title="Code health bounty",
            reward_mrwk="200",
            acceptance="Extract a coherent subsystem from app.main.",
        )

    with pytest.raises(ValueError, match=message):
        call_mcp_tool(sqlite_url, tool_name, arguments)
