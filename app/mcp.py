from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.ledger.service import LedgerError
from app.mcp_results import MCPTextResult

MCPToolHandler = Callable[[str, str, dict[str, Any]], str | dict[str, Any] | MCPTextResult]
MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SERVER_INFO = {"name": "mergework", "version": "0.1.0"}


MCP_BOUNTY_SUMMARY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Serialized MRWK bounty payload returned in structuredContent.",
    "properties": {
        "id": {"type": "integer", "minimum": 1},
        "repo": {"type": "string"},
        "issue_number": {"type": "integer", "minimum": 1},
        "issue_url": {"type": "string"},
        "title": {"type": "string"},
        "reward_mrwk": {"type": "string"},
        "available_mrwk": {"type": "string"},
        "reserved_mrwk": {"type": "string"},
        "max_awards": {"type": "integer", "minimum": 0},
        "awards_paid": {"type": "integer", "minimum": 0},
        "awards_remaining": {"type": "integer", "minimum": 0},
        "effective_available_mrwk": {"type": "string"},
        "effective_awards_remaining": {"type": "integer", "minimum": 0},
        "pending_payout_awards": {"type": "integer", "minimum": 0},
        "pending_payout_proposals": {"type": "array", "items": {"type": "object"}},
        "pending_close_proposal": {"type": ["object", "null"]},
        "availability_state": {"type": "string"},
        "availability_note": {"type": "string"},
        "submission_requirements": {"type": "object"},
        "status": {"type": "string"},
        "acceptance": {"type": "string"},
        "created_at": {"type": "string"},
        "active_attempt_count": {"type": "integer", "minimum": 0},
        "active_attempt_warnings": {"type": "array", "items": {"type": "string"}},
        "attempt_endpoint": {"type": "string"},
        "awards": {
            "type": "array",
            "description": "Present only when include_awards is true.",
            "items": {"type": "object"},
        },
    },
    "required": [
        "id",
        "repo",
        "issue_number",
        "issue_url",
        "title",
        "reward_mrwk",
        "available_mrwk",
        "reserved_mrwk",
        "max_awards",
        "awards_paid",
        "awards_remaining",
        "effective_available_mrwk",
        "effective_awards_remaining",
        "pending_payout_awards",
        "pending_payout_proposals",
        "pending_close_proposal",
        "availability_state",
        "availability_note",
        "submission_requirements",
        "status",
        "acceptance",
        "created_at",
    ],
    "additionalProperties": True,
}

MCP_BOUNTY_ATTEMPT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "minimum": 1},
        "bounty_id": {"type": "integer", "minimum": 1},
        "repo": {"type": "string"},
        "issue_number": {"type": "integer", "minimum": 1},
        "issue_url": {"type": "string"},
        "claimant": {"type": "string"},
        "comment_url": {"type": "string"},
        "expires_at": {"type": "string"},
        "created_at": {"type": "string"},
        "expired": {"type": "boolean"},
    },
    "required": [
        "id",
        "bounty_id",
        "repo",
        "issue_number",
        "issue_url",
        "claimant",
        "comment_url",
        "expires_at",
        "created_at",
        "expired",
    ],
    "additionalProperties": False,
}

MCP_BOUNTY_ATTEMPTS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "bounty_id": {"type": "integer", "minimum": 1},
        "issue_number": {"type": "integer", "minimum": 1},
        "status": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "attempts": {"type": "array", "items": MCP_BOUNTY_ATTEMPT_OUTPUT_SCHEMA},
    },
    "required": ["bounty_id", "issue_number", "status", "warnings", "attempts"],
    "additionalProperties": False,
}

MCP_LEDGER_ENTRY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Serialized public ledger entry returned in structuredContent.",
    "properties": {
        "sequence": {"type": "integer", "minimum": 1},
        "type": {"type": "string"},
        "from": {"type": ["string", "null"]},
        "to": {"type": "string"},
        "amount_mrwk": {"type": "string", "pattern": r"^\d+(?:\.\d{1,6})?$"},
        "reference": {"type": ["string", "null"]},
        "previous_hash": {"type": ["string", "null"]},
        "entry_hash": {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
            "pattern": "^[0-9a-f]{64}$",
        },
        "proof_hash": {
            "type": ["string", "null"],
            "minLength": 64,
            "maxLength": 64,
            "pattern": "^[0-9a-f]{64}$",
        },
        "created_at": {"type": "string"},
    },
    "required": [
        "sequence",
        "type",
        "from",
        "to",
        "amount_mrwk",
        "reference",
        "previous_hash",
        "entry_hash",
        "proof_hash",
        "created_at",
    ],
    "additionalProperties": False,
}

MCP_PROOF_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Public proof wrapper returned in structuredContent.",
    "properties": {
        "hash": {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
            "pattern": "^[0-9a-f]{64}$",
        },
        "kind": {"type": "string"},
        "ledger_sequence": {"type": "integer", "minimum": 1},
        "bounty_id": {"type": ["integer", "null"], "minimum": 1},
        "submission_id": {"type": ["integer", "null"], "minimum": 1},
        "created_at": {"type": "string"},
        "proof": {"type": "object", "additionalProperties": True},
    },
    "required": [
        "hash",
        "kind",
        "ledger_sequence",
        "bounty_id",
        "submission_id",
        "created_at",
        "proof",
    ],
    "additionalProperties": False,
}

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
        "outputSchema": {
            "type": "array",
            "items": MCP_BOUNTY_SUMMARY_OUTPUT_SCHEMA,
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
        "outputSchema": MCP_BOUNTY_SUMMARY_OUTPUT_SCHEMA,
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
        "outputSchema": MCP_BOUNTY_ATTEMPTS_OUTPUT_SCHEMA,
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
    {
        "name": "get_ledger_entry",
        "description": "Get a ledger entry by immutable ledger sequence",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Immutable public ledger sequence number.",
                },
            },
            "required": ["sequence"],
            "additionalProperties": False,
        },
        "outputSchema": MCP_LEDGER_ENTRY_OUTPUT_SCHEMA,
    },
    {
        "name": "get_proof",
        "description": "Get a public proof by hash",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hash": {
                    "type": "string",
                    "pattern": "^[0-9a-fA-F]{64}$",
                    "description": (
                        "Public proof hash returned by ledger, activity, or bounty APIs."
                    ),
                },
            },
            "required": ["hash"],
            "additionalProperties": False,
        },
        "outputSchema": MCP_PROOF_OUTPUT_SCHEMA,
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


def _tool_result_response(
    response_id: Any, tool_result: str | dict[str, Any] | MCPTextResult
) -> dict[str, Any]:
    if isinstance(tool_result, MCPTextResult):
        result: dict[str, Any] = {"content": [{"type": "text", "text": tool_result.text}]}
        if tool_result.structured_content is not None:
            result["structuredContent"] = tool_result.structured_content
        return {"jsonrpc": "2.0", "id": response_id, "result": result}
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
