from __future__ import annotations

import json

import pytest

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis
from app.mcp_tools import call_mcp_tool


from app.mcp_results import MCPTextResult

def _get_content(r: str | dict[str, object] | MCPTextResult) -> list | dict:
    """Extract json content from MCPTextResult or raw string."""
    if isinstance(r, MCPTextResult):
        return r.structured_content
    return json.loads(r)


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

    bounties = _get_content(result)
    assert bounties[0]["issue_number"] == 390
    assert bounties[0]["title"] == "Code health bounty"
    # Verify structured content is returned
    if isinstance(result, MCPTextResult):
        assert isinstance(result.structured_content, list)
        assert "structuredContent" in dir(result) or hasattr(result, "structured_content")


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

    bounties = _get_content(result)
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


# ─── Conformance: structuredContent output ────────────────────

def test_mcp_tool_list_bounties_returns_structured_content(sqlite_url: str) -> None:
    """Conformance: list_bounties returns structuredContent with full bounty payload."""
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=395,
            issue_url="https://github.com/ramimbo/mergework/issues/395",
            title="Conformance test bounty",
            reward_mrwk="100",
            acceptance="Structured conformance.",
        )

    result = call_mcp_tool(sqlite_url, "list_bounties", {"status": "open"})
    assert isinstance(result, MCPTextResult), "list_bounties should return MCPTextResult"
    assert result.structured_content is not None, "structured_content should not be None"
    assert isinstance(result.structured_content, list), "structured_content should be a list"
    assert len(result.structured_content) > 0, "structured_content should not be empty"
    assert result.structured_content[0]["issue_number"] == 395
    assert result.structured_content[0]["reward_mrwk"] == "100"
    assert "title" in result.structured_content[0]
    assert "id" in result.structured_content[0]
    # Human-readable text preserved
    assert "Conformance test bounty" in result.text


def test_mcp_tool_get_bounty_returns_structured_content(sqlite_url: str) -> None:
    """Conformance: get_bounty returns structuredContent with full bounty payload."""
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=396,
            issue_url="https://github.com/ramimbo/mergework/issues/396",
            title="Get bounty conformance",
            reward_mrwk="150",
            acceptance="Structured conformance for get_bounty.",
        )

    result = call_mcp_tool(sqlite_url, "get_bounty", {"id": bounty.id})
    assert isinstance(result, MCPTextResult), "get_bounty should return MCPTextResult"
    assert result.structured_content is not None
    assert result.structured_content["issue_number"] == 396
    assert result.structured_content["reward_mrwk"] == "150"
    assert result.structured_content["title"] == "Get bounty conformance"
    assert "Get bounty conformance" in result.text


def test_mcp_tool_get_balance_returns_structured_content(sqlite_url: str) -> None:
    """Conformance: get_balance returns structuredContent with account and balance."""
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    result = call_mcp_tool(sqlite_url, "get_balance", {"account": "treasury:mrwk"})
    assert isinstance(result, MCPTextResult), "get_balance should return MCPTextResult"
    assert result.structured_content is not None
    assert "account" in result.structured_content
    assert "balance_mrwk" in result.structured_content
    assert result.structured_content["account"] == "treasury:mrwk"
