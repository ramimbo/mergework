from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def mcp_tools_by_name(client: TestClient) -> dict[str, dict[str, Any]]:
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    payload = response.json()
    return {tool["name"]: tool for tool in payload["result"]["tools"]}


def mcp_tools_call(
    client: TestClient,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    request_id: int,
) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )
    assert response.status_code == 200
    return response.json()


def assert_mcp_tools_call_rejects(
    client: TestClient,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    request_id: int,
) -> None:
    payload = mcp_tools_call(
        client, tool_name=tool_name, arguments=arguments, request_id=request_id
    )
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == request_id
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == -32602
    assert error["message"] == "invalid tool arguments"


def assert_mcp_tools_call_accepts(
    client: TestClient,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    request_id: int,
) -> dict[str, Any]:
    payload = mcp_tools_call(
        client, tool_name=tool_name, arguments=arguments, request_id=request_id
    )
    assert "result" in payload
    assert "error" not in payload
    return payload["result"]
