from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.ledger.service import LedgerError

MCPToolHandler = Callable[[str, str, dict[str, Any]], str | dict[str, Any]]
MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SERVER_INFO = {"name": "mergework", "version": "0.1.0"}

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_bounties",
        "description": (
            "List MRWK bounties with optional status, q, sort, limit, and availability filters"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "paid", "closed"],
                    "default": "open",
                    "description": "Bounty status filter.",
                },
                "q": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Optional bounty text or issue-number search.",
                },
                "sort": {
                    "type": "string",
                    "enum": ["newest", "reward"],
                    "default": "newest",
                    "description": "Sort order for returned bounties.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 25,
                    "description": "Maximum number of bounties to return.",
                },
                "availability": {
                    "type": "string",
                    "enum": ["all", "effectively_open"],
                    "default": "all",
                    "description": "Optional effective availability filter.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_bounty",
        "description": (
            "Get a bounty by internal id or bounty_id alias, or by GitHub issue_number "
            "with optional repo, optionally with accepted awards"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Internal MRWK bounty id. Use one bounty selector.",
                },
                "bounty_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Alias for the internal MRWK bounty id.",
                },
                "issue_number": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "GitHub issue number for an MRWK bounty.",
                },
                "repo": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Optional owner/name repository scope for issue_number lookups.",
                },
                "include_awards": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include accepted award records in the bounty payload.",
                },
            },
            "oneOf": [
                {"required": ["id"]},
                {"required": ["bounty_id"]},
                {"required": ["issue_number"]},
            ],
            "dependentRequired": {"repo": ["issue_number"]},
            "additionalProperties": False,
        },
    },
    {
        "name": "list_bounty_attempts",
        "description": (
            "List advisory active-attempt reservations for a bounty by internal bounty_id "
            "(or id alias), or by GitHub issue_number with optional repo"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Alias for the internal MRWK bounty id.",
                },
                "bounty_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Internal MRWK bounty id.",
                },
                "issue_number": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "GitHub issue number for an MRWK bounty.",
                },
                "repo": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Optional owner/name repository scope for issue_number lookups.",
                },
                "include_expired": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include expired advisory attempts as well as active ones.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 25,
                    "description": "Maximum attempt records to return.",
                },
            },
            "oneOf": [
                {"required": ["id"]},
                {"required": ["bounty_id"]},
                {"required": ["issue_number"]},
            ],
            "dependentRequired": {"repo": ["issue_number"]},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_balance",
        "description": "Get an account balance",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Account selector such as github:<login>, treasury:mrwk, "
                        "reserve:bounty:<id>, or an mrwk1 wallet address."
                    ),
                },
            },
            "required": ["account"],
            "additionalProperties": False,
        },
    },
    {
        "name": "register_wallet",
        "description": "Register an MRWK wallet public key",
    },
    {
        "name": "get_wallet",
        "description": "Get an MRWK wallet by address",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "pattern": "^[mM][rR][wW][kK]1[0-9a-fA-F]{40}$",
                    "description": "MRWK wallet address, using the mrwk1 prefix and 40 hex chars.",
                },
            },
            "required": ["address"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_wallet_transfer",
        "description": "Submit a signed MRWK wallet transfer",
    },
    {"name": "get_ledger_entry", "description": "Get a ledger entry"},
    {"name": "get_proof", "description": "Get a public proof by hash"},
    {
        "name": "submit_work_proof",
        "description": (
            "Return submission instructions for bounty_id or issue_number, optionally "
            "scoping issue_number by repo, with text or json format"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bounty_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Internal MRWK bounty id. Use either bounty_id or issue_number.",
                },
                "issue_number": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "GitHub issue number for an MRWK bounty. "
                        "Use either issue_number or bounty_id."
                    ),
                },
                "repo": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Optional owner/name repository scope for issue_number lookups.",
                },
                "format": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "default": "text",
                    "description": "Use json for machine-readable structuredContent guidance.",
                },
            },
            "additionalProperties": False,
            "allOf": [
                {"not": {"required": ["bounty_id", "issue_number"]}},
                {
                    "if": {"required": ["repo"]},
                    "then": {
                        "required": ["issue_number"],
                        "not": {"required": ["bounty_id"]},
                    },
                },
                {"if": {"required": ["bounty_id"]}, "then": {"not": {"required": ["repo"]}}},
            ],
        },
    },
]


def _jsonrpc_error(response_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": response_id, "error": {"code": code, "message": message}}


def _initialize_response(response_id: Any, params: Any) -> dict[str, Any]:
    protocol_version = MCP_PROTOCOL_VERSION
    if (
        isinstance(params, dict)
        and isinstance(params.get("protocolVersion"), str)
        and params["protocolVersion"] == MCP_PROTOCOL_VERSION
    ):
        protocol_version = params["protocolVersion"]
    return {
        "jsonrpc": "2.0",
        "id": response_id,
        "result": {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": MCP_SERVER_INFO,
        },
    }


def _structured_json_payload(text: str) -> dict[str, Any] | list[Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, (dict, list)):
        return payload
    return None


def _tool_result_response(response_id: Any, tool_result: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(tool_result, dict):
        return {
            "jsonrpc": "2.0",
            "id": response_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(tool_result)}],
                "structuredContent": tool_result,
            },
        }
    structured_payload = _structured_json_payload(tool_result)
    if structured_payload is not None:
        return {
            "jsonrpc": "2.0",
            "id": response_id,
            "result": {
                "content": [{"type": "text", "text": tool_result}],
                "structuredContent": structured_payload,
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": response_id,
        "result": {"content": [{"type": "text", "text": tool_result}]},
    }


async def handle_mcp_request(
    request: Request, database_url: str, call_tool: MCPToolHandler
) -> dict[str, Any] | JSONResponse:
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse(_jsonrpc_error(None, -32700, "parse error"), status_code=400)

    if not isinstance(payload, dict):
        return JSONResponse(_jsonrpc_error(None, -32600, "invalid request"), status_code=400)

    response_id = payload.get("id")
    method = payload.get("method")
    if method == "initialize":
        return _initialize_response(response_id, payload.get("params"))

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": response_id, "result": {"tools": MCP_TOOLS}}

    if method != "tools/call":
        return _jsonrpc_error(response_id, -32601, "unknown method")

    params = payload.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _jsonrpc_error(response_id, -32602, "invalid params")

    name = params.get("name")
    args = params.get("arguments", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _jsonrpc_error(response_id, -32602, "invalid params")
    if not isinstance(name, str):
        return _jsonrpc_error(response_id, -32602, "tool name is required")

    try:
        tool_result = call_tool(database_url, name, args)
    except (KeyError, TypeError, ValueError, LedgerError, HTTPException):
        return _jsonrpc_error(response_id, -32602, "invalid tool arguments")

    return _tool_result_response(response_id, tool_result)
