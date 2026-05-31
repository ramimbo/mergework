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
        "description": (
            "List MRWK bounties with optional status, q, sort, limit, and availability filters"
        ),
    },
    {
        "name": "get_bounty",
        "description": "Get a bounty by id, optionally with accepted awards",
    },
    {
        "name": "list_bounty_attempts",
        "description": "List advisory active-attempt reservations for a bounty",
    },
    {"name": "get_balance", "description": "Get an account balance"},
    {
        "name": "register_wallet",
        "description": "Register an MRWK wallet public key",
    },
    {"name": "get_wallet", "description": "Get an MRWK wallet by address"},
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
            "not": {"required": ["bounty_id", "issue_number"]},
        },
    },
]


def _jsonrpc_error(
    response_id: Any, code: int, message: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": response_id, "error": error}


def _argument_from_reason(reason: str, args: dict[str, Any]) -> str | None:
    for argument in sorted(args):
        if not isinstance(argument, str):
            continue
        if reason.startswith(f"{argument} ") or reason.startswith(f"use {argument} "):
            return argument
    return None


def _safe_argument_error_data(
    error: BaseException,
    *,
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any] | None:
    reason: str | None = None
    argument: str | None = None
    if isinstance(error, KeyError) and len(error.args) == 1 and isinstance(error.args[0], str):
        argument = error.args[0]
        reason = f"{argument} is required"
    elif isinstance(error, HTTPException):
        if error.status_code >= 500 or not isinstance(error.detail, str):
            return None
        reason = error.detail
    elif isinstance(error, ValueError | LedgerError):
        reason = str(error)
        if reason == "unknown tool":
            return None
    if not reason:
        return None

    data: dict[str, Any] = {"reason": reason, "tool": tool_name}
    if argument is None:
        argument = _argument_from_reason(reason, args)
    if argument is not None:
        data["argument"] = argument
    return data


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
    except HTTPException as exc:
        if exc.status_code >= 500:
            return _jsonrpc_error(response_id, -32603, "internal error")
        return _jsonrpc_error(
            response_id,
            -32602,
            "invalid tool arguments",
            _safe_argument_error_data(exc, tool_name=name, args=args),
        )
    except (KeyError, ValueError, LedgerError) as exc:
        return _jsonrpc_error(
            response_id,
            -32602,
            "invalid tool arguments",
            _safe_argument_error_data(exc, tool_name=name, args=args),
        )
    except TypeError:
        return _jsonrpc_error(response_id, -32602, "invalid tool arguments")

    return _tool_result_response(response_id, tool_result)
