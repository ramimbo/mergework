from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.ledger.service import LedgerError

MCPToolHandler = Callable[[str, str, dict[str, Any]], str | dict[str, Any]]

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_bounties",
        "description": "List MRWK bounties with optional status, q, sort, and limit filters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "paid", "closed"],
                    "default": "open",
                    "description": "Filter bounties by status.",
                },
                "q": {
                    "type": "string",
                    "description": (
                        "Case-insensitive search across repo, title, acceptance, or issue number."
                    ),
                },
                "sort": {
                    "type": "string",
                    "enum": ["newest", "reward", "available", "awards"],
                    "default": "newest",
                    "description": "Sort order for the returned bounty rows.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 25,
                    "description": "Maximum bounty rows to return.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_bounty",
        "description": "Get a bounty by id, optionally with accepted awards",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Internal MRWK bounty id.",
                },
                "include_awards": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include accepted award rows in the response.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_bounty_attempts",
        "description": "List advisory active-attempt reservations for a bounty",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bounty_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Internal MRWK bounty id.",
                },
                "include_expired": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include expired attempt reservations.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 25,
                    "description": "Maximum attempt rows to return.",
                },
            },
            "required": ["bounty_id"],
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
                    "description": "Ledger account id such as github:alice or mrwk1...",
                },
            },
            "required": ["account"],
            "additionalProperties": False,
        },
    },
    {
        "name": "register_wallet",
        "description": "Register an MRWK wallet public key",
        "inputSchema": {
            "type": "object",
            "properties": {
                "public_key_hex": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Wallet public key in hex form.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional public wallet label.",
                },
            },
            "required": ["public_key_hex"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_wallet",
        "description": "Get an MRWK wallet by address",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Registered mrwk1 wallet address.",
                },
            },
            "required": ["address"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_wallet_transfer",
        "description": "Submit a signed MRWK wallet transfer",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_address": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Sender mrwk1 wallet address.",
                },
                "to_address": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Recipient mrwk1 wallet address.",
                },
                "amount_mrwk": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Amount to transfer in MRWK.",
                },
                "nonce": {
                    "type": "integer",
                    "description": "Sender wallet nonce for this transfer.",
                },
                "memo": {"type": "string", "description": "Optional public transfer memo."},
                "signature_hex": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Hex signature authorizing the transfer.",
                },
            },
            "required": ["from_address", "to_address", "amount_mrwk", "nonce", "signature_hex"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_ledger_entry",
        "description": "Get a ledger entry",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Ledger sequence number.",
                },
            },
            "required": ["sequence"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_proof",
        "description": "Get a public proof by hash",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hash": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Public proof hash.",
                },
            },
            "required": ["hash"],
            "additionalProperties": False,
        },
    },
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
            "not": {"required": ["bounty_id", "issue_number"]},
        },
    },
]


def _jsonrpc_error(response_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": response_id, "error": {"code": code, "message": message}}


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
