from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.ledger.service import LedgerError
from app.models import Bounty
from app.serializers import bounty_to_dict

MCPToolHandler = Callable[[str, str, dict[str, Any]], str | dict[str, Any]]

MCP_TOOLS: list[dict[str, str]] = [
    {
        "name": "list_bounties",
        "description": "List MRWK bounties with optional status, q, and limit filters",
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
        "description": "Return submission instructions, optionally for a bounty_id or issue_number",
    },
]


def work_proof_guidance(bounty: Bounty) -> str:
    bounty_data = bounty_to_dict(bounty)
    availability = (
        "open for submissions"
        if bounty_data["status"] == "open" and bounty_data["awards_remaining"] > 0
        else "not currently open for new submissions"
    )
    return "\n".join(
        [
            f"Bounty #{bounty_data['issue_number']}: {bounty_data['title']}",
            f"Internal bounty id: {bounty_data['id']}",
            f"Repository: {bounty_data['repo']}",
            f"Issue: {bounty_data['issue_url']}",
            (
                f"Status: {bounty_data['status']} ({availability}); "
                f"awards remaining: {bounty_data['awards_remaining']} "
                f"of {bounty_data['max_awards']}"
            ),
            f"Reward: {bounty_data['reward_mrwk']} MRWK per accepted award",
            f"Acceptance: {bounty_data['acceptance']}",
            (
                "Submit: open a focused PR or issue that links this bounty, include "
                "specific test or behavior evidence, then comment /claim with the PR "
                "or evidence URL and verification summary."
            ),
            (
                "Do not include private keys, seed material, secrets, deployment "
                "credentials, private vulnerability details, or price claims."
            ),
        ]
    )


def work_proof_guidance_json(bounty: Bounty) -> dict[str, Any]:
    bounty_data = bounty_to_dict(bounty)
    can_submit = bounty_data["status"] == "open" and bounty_data["awards_remaining"] > 0
    availability_warnings: list[str] = []
    if bounty_data["status"] != "open":
        availability_warnings.append(f"bounty is {bounty_data['status']}")
    if bounty_data["awards_remaining"] <= 0:
        availability_warnings.append("bounty has no award slots remaining")
    return {
        "bounty_id": bounty_data["id"],
        "issue_number": bounty_data["issue_number"],
        "status": bounty_data["status"],
        "availability": "open_for_submissions" if can_submit else "not_currently_open",
        "can_submit": can_submit,
        "availability_warnings": availability_warnings,
        "awards_remaining": bounty_data["awards_remaining"],
        "max_awards": bounty_data["max_awards"],
        "awards_paid": bounty_data["awards_paid"],
        "reward_mrwk": bounty_data["reward_mrwk"],
        "available_mrwk": bounty_data["available_mrwk"],
        "repository": bounty_data["repo"],
        "issue_url": bounty_data["issue_url"],
        "title": bounty_data["title"],
        "acceptance": bounty_data["acceptance"],
        "submission_format": (
            "Open a focused PR or issue that links this bounty, include specific "
            "test or behavior evidence, then comment /claim with the PR or "
            "evidence URL and verification summary."
        ),
        "safety_rules": [
            "Do not include private keys, seed material, secrets, deployment "
            "credentials, private vulnerability details, or price claims."
        ],
    }


def generic_work_proof_guidance_json() -> dict[str, Any]:
    return {
        "bounty_id": None,
        "issue_number": None,
        "status": "generic_guidance",
        "availability": "unknown_without_bounty",
        "can_submit": None,
        "availability_warnings": [],
        "awards_remaining": None,
        "reward_mrwk": None,
        "repository": None,
        "issue_url": None,
        "acceptance": None,
        "submission_format": (
            "Open a focused PR or issue, reference the MRWK bounty, include test "
            "evidence, and wait for a maintainer to apply mrwk:accepted."
        ),
        "safety_rules": [
            "Do not include private keys, seed material, secrets, deployment "
            "credentials, private vulnerability details, or price claims."
        ],
    }


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
