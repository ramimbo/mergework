from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app

@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "params", "expected_key"),
    [
        ("list_bounties", {"status": "open"}, "bounties"),
        ("get_wallet", {"address": "mrwk1test"}, "wallet"),
        ("list_bounty_attempts", {"bounty_id": 1}, "attempts"),
        ("list_bounty_attempts", {"issue_number": 999}, "attempts"),
    ],
)
def test_mcp_tools_return_structured_content(
    client: TestClient,
    method: str,
    params: dict[str, Any],
    expected_key: str,
) -> None:
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": method,
            "arguments": params,
        },
        "id": 1,
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "result" in data, f"No result for {method}: {data}"
    result = data["result"]
    # Ensure structuredContent exists and contains expected top-level key
    structured = result.get("content", [])
    assert any(
        item.get("type") == "resource" and expected_key in item.get("resource", {}).get("text", "")
        for item in structured
    ), f"structuredContent missing {expected_key} in {method}: {structured}"


def test_tools_list_schema_consistent_with_call(client: TestClient) -> None:
    """Verify each tool's inputSchema accepts only calls that succeed or fail with clear validation."""
    # Get advertised tool schemas
    tools_response = client.post("/mcp", json={
        "jsonrpc": "2.0",
        "method": "tools/list",
        "params": {},
        "id": 1,
    })
    assert tools_response.status_code == 200
    tools_data = tools_response.json()
    tools = tools_data.get("result", {}).get("tools", [])
    assert len(tools) > 0, "No tools advertised"

    for tool in tools:
        name = tool.get("name", "")
        schema = tool.get("inputSchema", {})
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        # Test valid minimal call (empty params if no required fields)
        call_payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": {}},
            "id": 2,
        }
        call_response = client.post("/mcp", json=call_payload)
        assert call_response.status_code == 200
        call_data = call_response.json()
        # Should either succeed or return a validation error with clear message
        if "error" in call_data:
            error_msg = call_data["error"].get("message", "").lower()
            assert any(
                word in error_msg
                for word in ["required", "invalid", "missing", "type"]
            ), f"Unclear error for {name}: {error_msg}"


def test_invalid_argument_error_safe(client: TestClient) -> None:
    """Passing invalid types or values should not cause server error."""
    bad_calls = [
        {"name": "get_wallet", "arguments": {"address": 123}},  # wrong type
        {"name": "list_bounty_attempts", "arguments": {"bounty_id": "abc"}},
        {"name": "list_bounties", "arguments": {"status": "nonexistent"}},
    ]
    for call in bad_calls:
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": call,
            "id": 3,
        }
        response = client.post("/mcp", json=payload)
        assert response.status_code == 200  # JSON-RPC always 200
        data = response.json()
        if "error" in data:
            # Ensure error message is informative
            assert any(
                word in data["error"].get("message", "").lower()
                for word in ["invalid", "type", "error", "not", "must"]
            ), f"Poor error for {call}: {data}"
        else:
            # If no error, result should be present (maybe fallback)
            assert "result" in data
