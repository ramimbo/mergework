from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import (
    GENESIS_SUPPLY_MICRO,
    create_bounty,
    ensure_genesis,
    pay_bounty,
)
from app.main import create_app
from app.mcp import MCP_TOOLS


def _assert_invalid_tool_arguments_envelope(
    response_json: dict[str, object],
    *,
    request_id: object,
    expected_data: object = None,
) -> None:
    assert response_json["jsonrpc"] == "2.0"
    assert response_json["id"] == request_id
    error = response_json["error"]
    assert isinstance(error, dict)
    assert error["code"] == -32602
    assert error["message"] == "invalid tool arguments"
    if expected_data is None:
        return
    if expected_data is False:
        assert "data" not in error
        return
    assert error["data"] == expected_data


def _tool_by_name(name: str) -> dict[str, Any]:
    return next(tool for tool in MCP_TOOLS if tool["name"] == name)


def _tools_with_output_schema() -> list[dict[str, Any]]:
    return [tool for tool in MCP_TOOLS if "outputSchema" in tool]


def _assert_structured_matches_required(
    payload: dict[str, Any] | list[Any], schema: dict[str, Any]
) -> None:
    if schema.get("type") == "array":
        assert isinstance(payload, list)
        item_schema = schema["items"]
        for item in payload:
            assert isinstance(item, dict)
            for key in item_schema.get("required", []):
                assert key in item
        return
    assert isinstance(payload, dict)
    for key in schema.get("required", []):
        assert key in payload


@pytest.mark.parametrize("tool", _tools_with_output_schema(), ids=lambda tool: tool["name"])
def test_mcp_tools_list_output_schema_required_fields_are_documented(
    tool: dict[str, Any],
) -> None:
    schema = tool["outputSchema"]
    assert schema.get("type") in {"object", "array"}
    if schema.get("type") == "object":
        assert isinstance(schema.get("required"), list)
        assert schema["required"]


def test_mcp_schema_conformance_balance_wallet_ledger_and_proof(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=946,
            issue_url="https://github.com/ramimbo/mergework/issues/946",
            title="MCP schema conformance",
            reward_mrwk="150",
            acceptance="Schema conformance tests.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/946",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        ledger_sequence = proof.ledger_sequence

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
    tools_by_name = {tool["name"]: tool for tool in tools["result"]["tools"]}

    balance = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_balance", "arguments": {"account": "treasury:mrwk"}},
        },
    ).json()["result"]
    balance_payload = balance["structuredContent"]
    _assert_structured_matches_required(
        balance_payload, tools_by_name["get_balance"]["outputSchema"]
    )
    assert balance_payload["balance_microunits"] == GENESIS_SUPPLY_MICRO - 150_000_000

    public_key_hex = "33" * 32
    registered = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "register_wallet",
                "arguments": {"public_key_hex": public_key_hex, "label": "schema wallet"},
            },
        },
    ).json()["result"]
    wallet_payload = registered["structuredContent"]
    _assert_structured_matches_required(
        wallet_payload, tools_by_name["register_wallet"]["outputSchema"]
    )

    fetched = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_wallet", "arguments": {"address": wallet_payload["address"]}},
        },
    ).json()["result"]
    _assert_structured_matches_required(
        fetched["structuredContent"], tools_by_name["get_wallet"]["outputSchema"]
    )

    ledger = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "get_ledger_entry",
                "arguments": {"sequence": ledger_sequence},
            },
        },
    ).json()["result"]
    ledger_payload = ledger["structuredContent"]
    _assert_structured_matches_required(
        ledger_payload, tools_by_name["get_ledger_entry"]["outputSchema"]
    )

    proof_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "get_proof", "arguments": {"hash": proof.hash}},
        },
    ).json()["result"]
    proof_payload = proof_result["structuredContent"]
    _assert_structured_matches_required(proof_payload, tools_by_name["get_proof"]["outputSchema"])


def test_mcp_list_bounties_rejects_unknown_argument_with_safe_error_data(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "list_bounties",
                "arguments": {"status": "open", "unexpected_field": "drop-me"},
            },
        },
    )

    payload = response.json()
    _assert_invalid_tool_arguments_envelope(
        payload,
        request_id=7,
        expected_data={
            "code": "invalid_argument",
            "tool": "list_bounties",
            "field": None,
            "message": "unknown argument",
        },
    )
    assert "drop-me" not in json.dumps(payload)


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("get_balance", {"account": "treasury:mrwk", "extra": 1}),
        ("get_wallet", {"address": "mrwk1" + ("a" * 40), "label": "ignored"}),
        ("get_ledger_entry", {"sequence": 1, "offset": 0}),
        ("get_proof", {"hash": "0" * 64, "kind": "ignored"}),
    ],
)
def test_mcp_read_tools_reject_unknown_arguments(
    sqlite_url: str, tool_name: str, arguments: dict[str, object]
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )

    payload = response.json()
    _assert_invalid_tool_arguments_envelope(payload, request_id=8)
    assert payload["error"].get("data", {}).get("message") == "unknown argument"
