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

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_bounties",
        "description": (
            "List MRWK bounties with optional status, q, repo, issue_number, "
            "sort, limit, and availability filters"
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
                "repo": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Optional owner/name repository filter.",
                },
                "issue_number": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional GitHub issue-number filter.",
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
        "inputSchema": {
            "type": "object",
            "properties": {
                "public_key_hex": {
                    "type": "string",
                    "pattern": "^[0-9a-fA-F]{64}$",
                    "description": "Ed25519 public key as 32 bytes of hex.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional human-readable wallet label.",
                },
            },
            "required": ["public_key_hex"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "pattern": "^[mM][rR][wW][kK]1[0-9a-fA-F]{40}$",
                },
                "public_key_hex": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
                "label": {"type": ["string", "null"]},
                "github_login": {"type": ["string", "null"]},
                "balance_mrwk": {"type": "string"},
                "nonce": {"type": "integer", "minimum": 0},
                "next_nonce": {"type": "integer", "minimum": 1},
                "created_at": {"type": "string"},
            },
            "required": [
                "address",
                "public_key_hex",
                "label",
                "github_login",
                "balance_mrwk",
                "nonce",
                "next_nonce",
                "created_at",
            ],
            "additionalProperties": True,
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


# Static whitelist of registered MCP tool names, derived once at import time
# from :data:`MCP_TOOLS`. The dispatcher uses this set to bound the
# ``data["tool"]`` slot of the additive field-level error envelope: only
# names that actually belong to a registered tool are surfaced, so an
# unknown-tool ``ValueError`` (whose ``tool_name`` is, by definition, not in
# the set) cannot echo arbitrary caller input into the response body.
_KNOWN_TOOL_NAMES: frozenset[str] = frozenset(tool["name"] for tool in MCP_TOOLS)


def _jsonrpc_error(response_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": response_id, "error": {"code": code, "message": message}}


# Field-level error metadata for the additive `error.data` payload. The
# dispatcher surfaces this only when a `ValueError` raised by
# :func:`call_mcp_tool` matches a whitelisted safe phrase. Untrusted caller
# input is never echoed: `field` must be one of `_KNOWN_TOOL_FIELDS` and
# `message` must be one of `_KNOWN_FIELD_MESSAGES` or `None` (field-less).
_KNOWN_TOOL_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "bounty_id",
        "issue_number",
        "repo",
        "include_awards",
        "include_expired",
        "limit",
        "q",
        "sort",
        "availability",
        "status",
        "account",
        "address",
        "public_key_hex",
        "label",
        "from_address",
        "to_address",
        "amount_mrwk",
        "nonce",
        "memo",
        "signature_hex",
        "sequence",
        "hash",
        "format",
    }
)

# Safe, no-user-input phrases recognized as the trailing half of a
# field-prefixed `ValueError` message. Values are the strings that callers
# will see in `error.data.message`; they are never derived from caller input.
_KNOWN_FIELD_MESSAGES: dict[str, str] = {
    "must be an integer": "must be an integer",
    "must be a string": "must be a string",
    "must be a boolean": "must be a boolean",
    "must be positive": "must be positive",
    "must not be empty": "must not be empty",
    "must not contain control characters": "must not contain control characters",
    "is too large": "is too large",
    "is too long": "is too long",
    "must be at most 100": "must be at most 100",
    "must be at most 500 characters": "must be at most 500 characters",
    "must be one of: open, paid, closed": "must be one of: open, paid, closed",
    "must be text or json": "must be text or json",
}

# Field-less safe phrases emitted when the underlying message is not
# field-prefixed (e.g. `unknown tool`).
_KNOWN_FIELDLESS_MESSAGES: dict[str, str] = {
    "unknown tool": "unknown tool",
    "matches multiple bounties": "matches multiple bounties",
    "repo can only be used with issue_number": ("repo can only be used with issue_number"),
}


def _classify_value_error(exc: ValueError) -> dict[str, Any] | None:
    """Map a ``ValueError`` raised by :func:`call_mcp_tool` to a safe
    field-level error data payload.

    Returns a ``{"code", "tool", "field", "message"}`` dict built only from
    the static ``_KNOWN_TOOL_FIELDS`` / ``_KNOWN_FIELD_MESSAGES`` /
    ``_KNOWN_FIELDLESS_MESSAGES`` whitelists when the original message
    matches, or ``None`` if it does not — in which case the dispatcher
    returns the legacy envelope without ``error.data`` so untrusted caller
    input never reaches the response.

    The recognised patterns are:

    - ``"<field> <safe phrase>"`` where ``<field>`` is a known tool field
      and ``<safe phrase>`` is a known field-level message.
    - A field-less known message (e.g. ``"unknown tool"``).
    """
    if not exc.args:
        return None
    message = str(exc.args[0])
    if not message:
        return None

    # Field-prefixed: "<field> <safe phrase>". Split on the first whitespace
    # only; the field token is the first identifier-shaped run and the rest
    # of the message must match a whitelisted safe phrase verbatim. This
    # keeps the surface area to a small finite set of literal strings.
    head, sep, tail = message.partition(" ")
    if sep and head in _KNOWN_TOOL_FIELDS and tail in _KNOWN_FIELD_MESSAGES:
        return {
            "code": "invalid_argument",
            "field": head,
            "message": _KNOWN_FIELD_MESSAGES[tail],
        }

    # Some upstream ``ValueError`` messages are emitted as
    # ``"<field> <field-less safe phrase>"`` even when the phrase itself is
    # semantically field-less (e.g. ``"issue_number matches multiple
    # bounties"``). Treat that case as a field-less known phrase so the
    # whitelist remains reachable for those callsites; the leading field
    # token is dropped from the response because the message is the
    # user-facing signal and the field is the static name the dispatcher
    # would have surfaced anyway.
    if sep and head in _KNOWN_TOOL_FIELDS and tail in _KNOWN_FIELDLESS_MESSAGES:
        return {
            "code": "invalid_argument",
            "field": None,
            "message": _KNOWN_FIELDLESS_MESSAGES[tail],
        }

    # Field-less safe phrases.
    if message in _KNOWN_FIELDLESS_MESSAGES:
        return {
            "code": "invalid_argument",
            "field": None,
            "message": _KNOWN_FIELDLESS_MESSAGES[message],
        }

    return None


def _invalid_tool_arguments_response(
    response_id: Any, tool_name: str, exc: ValueError
) -> dict[str, Any]:
    """Return the legacy ``-32602 invalid tool arguments`` envelope with an
    optional, additive ``error.data`` payload for safe argument-validation
    failures.

    The JSON-RPC ``error.message`` always stays exactly
    ``"invalid tool arguments"`` for backward compatibility. The new
    ``error.data`` is attached only when the underlying ``ValueError``
    message matches a whitelisted safe phrase, so untrusted caller input
    never reaches the response.
    """
    error_payload: dict[str, Any] = {"code": -32602, "message": "invalid tool arguments"}
    classified = _classify_value_error(exc)
    if classified is not None:
        # Bound the ``data["tool"]`` slot to the static
        # :data:`_KNOWN_TOOL_NAMES` whitelist. Field-level argument
        # validation always runs *after* the dispatcher has already
        # resolved ``tool_name`` to a registered tool, so the bound
        # value is identical to ``tool_name`` on the success path. For
        # the ``unknown tool`` path the rejected caller name is, by
        # definition, not in the whitelist; reflecting it would surface
        # untrusted caller input, so the slot is left as ``None``.
        safe_tool = tool_name if tool_name in _KNOWN_TOOL_NAMES else None
        error_payload["data"] = {
            "code": classified["code"],
            "tool": safe_tool,
            "field": classified["field"],
            "message": classified["message"],
        }
    return {"jsonrpc": "2.0", "id": response_id, "error": error_payload}


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
    except ValueError as exc:
        return _invalid_tool_arguments_response(response_id, name, exc)
    except (KeyError, TypeError, LedgerError, HTTPException):
        return _jsonrpc_error(response_id, -32602, "invalid tool arguments")

    return _tool_result_response(response_id, tool_result)
