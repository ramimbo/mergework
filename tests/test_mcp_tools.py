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


def test_call_mcp_tool_filters_bounties_by_repo_and_issue_number(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=390,
            issue_url="https://github.com/ramimbo/mergework/issues/390",
            title="Primary repo bounty",
            reward_mrwk="200",
            acceptance="Repo filters should skip this bounty.",
        )
        target = create_bounty(
            session,
            repo="Example/MergeWork",
            issue_number=390,
            issue_url="https://github.com/example/mergework/issues/390",
            title="Target repo bounty",
            reward_mrwk="200",
            acceptance="Repo and issue filters should keep this bounty.",
        )

    result = call_mcp_tool(
        sqlite_url,
        "list_bounties",
        {"repo": "example/mergework", "issue_number": 390},
    )

    bounties = json.loads(result)
    assert [bounty["id"] for bounty in bounties] == [target.id]


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


def test_call_mcp_tool_reports_attempt_id_alias_issue_number_mix(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=391,
            issue_url="https://github.com/ramimbo/mergework/issues/391",
            title="Attempt alias issue-number conflict",
            reward_mrwk="200",
            acceptance="Agents should get actionable selector errors.",
        )
        bounty_id = bounty.id

    with pytest.raises(ValueError, match="use id or issue_number, not both"):
        call_mcp_tool(
            sqlite_url,
            "list_bounty_attempts",
            {"id": bounty_id, "issue_number": 391},
        )


def test_call_mcp_tool_rejects_c1_status_before_normalizing(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    with pytest.raises(ValueError, match="status must not contain control characters"):
        call_mcp_tool(sqlite_url, "list_bounties", {"status": "\u0085open"})


def test_call_mcp_tool_rejects_c1_work_proof_format_before_normalizing(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    with pytest.raises(ValueError, match="format must not contain control characters"):
        call_mcp_tool(sqlite_url, "submit_work_proof", {"format": "\u0085json"})


def test_call_mcp_tool_rejects_c1_nonce_before_integer_parsing(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    with pytest.raises(ValueError, match="nonce must not contain control characters"):
        call_mcp_tool(
            sqlite_url,
            "submit_wallet_transfer",
            {
                "from_address": "mrwk1" + ("a" * 40),
                "to_address": "mrwk1" + ("b" * 40),
                "amount_mrwk": "1",
                "nonce": "\u00851",
                "memo": "",
                "signature_hex": "00" * 64,
            },
        )
