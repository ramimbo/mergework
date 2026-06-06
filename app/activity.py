from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.accounts import normalized_account
from app.control_chars import contains_control_character
from app.db import session_scope
from app.query_validation import reject_control_char_query_param, reject_repeated_query_param
from app.serializers import activity_to_dict


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


ACTIVITY_RESPONSE_SCHEMA = {
    "description": "Public activity feed with accepted and pending MRWK bounty work.",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "required": [
                    "totals",
                    "pending_totals",
                    "query",
                    "contributors",
                    "pending_payouts",
                    "recent",
                ],
                "properties": {
                    "totals": {
                        "type": "object",
                        "required": ["accepted_awards", "accepted_mrwk", "contributors"],
                        "properties": {
                            "accepted_awards": {"type": "integer", "minimum": 0},
                            "accepted_mrwk": {"type": "string"},
                            "contributors": {"type": "integer", "minimum": 0},
                        },
                    },
                    "pending_totals": {
                        "type": "object",
                        "required": ["pending_awards", "pending_mrwk"],
                        "properties": {
                            "pending_awards": {"type": "integer", "minimum": 0},
                            "pending_mrwk": {"type": "string"},
                        },
                    },
                    "query": {"type": "string"},
                    "account": _nullable({"type": "string"}),
                    "contributors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["account", "accepted_awards", "accepted_mrwk"],
                            "properties": {
                                "account": _nullable({"type": "string"}),
                                "accepted_awards": {"type": "integer", "minimum": 0},
                                "accepted_mrwk": {"type": "string"},
                                "latest_submission_url": _nullable({"type": "string"}),
                                "latest_bounty_repo": _nullable({"type": "string"}),
                                "latest_bounty_issue_number": _nullable({"type": "integer"}),
                                "latest_bounty_issue_url": _nullable({"type": "string"}),
                                "latest_proof_hash": _nullable({"type": "string"}),
                                "latest_proof_url": _nullable({"type": "string"}),
                            },
                        },
                    },
                    "pending_payouts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "proposal_id",
                                "proposal_url",
                                "status",
                                "account",
                                "bounty_id",
                                "bounty_url",
                            ],
                            "properties": {
                                "proposal_id": {"type": "integer"},
                                "proposal_url": {"type": "string"},
                                "status": {"type": "string"},
                                "account": _nullable({"type": "string"}),
                                "amount_mrwk": _nullable({"type": "string"}),
                                "submission_url": _nullable({"type": "string"}),
                                "bounty_repo": _nullable({"type": "string"}),
                                "bounty_issue_number": _nullable({"type": "integer"}),
                                "bounty_issue_url": _nullable({"type": "string"}),
                                "bounty_id": _nullable({"type": "integer"}),
                                "bounty_url": _nullable({"type": "string"}),
                                "accepted_by": _nullable({"type": "string"}),
                                "proposed_at": {"type": "string"},
                                "executes_after": {"type": "string"},
                            },
                        },
                    },
                    "recent": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "ledger_sequence",
                                "account",
                                "amount_mrwk",
                                "submission_url",
                                "proof_hash",
                                "proof_url",
                                "created_at",
                            ],
                            "properties": {
                                "ledger_sequence": {"type": "integer"},
                                "account": _nullable({"type": "string"}),
                                "amount_mrwk": _nullable({"type": "string"}),
                                "submission_url": _nullable({"type": "string"}),
                                "bounty_repo": _nullable({"type": "string"}),
                                "bounty_issue_number": _nullable({"type": "integer"}),
                                "bounty_issue_url": _nullable({"type": "string"}),
                                "proof_hash": {"type": "string"},
                                "proof_url": {"type": "string"},
                                "bounty_id": _nullable({"type": "integer"}),
                                "bounty_url": _nullable({"type": "string"}),
                                "created_at": {"type": "string"},
                            },
                        },
                    },
                    "api_activity_url": {"type": "string"},
                    "clear_activity_url": {"type": "string"},
                },
            }
        }
    },
}


def activity_context(
    session: Session, query: str | None = None, account: str | None = None
) -> dict[str, Any]:
    if query is not None and contains_control_character(query):
        raise HTTPException(status_code=400, detail="q must not contain control characters")
    normalized = normalized_account(account) if account is not None else None
    context = activity_to_dict(session, query, account=normalized)
    context["api_activity_url"] = _activity_api_url(context["query"], context.get("account"))
    context["clear_activity_url"] = _activity_page_url(context.get("account"))
    return context


def _activity_api_url(query: str, account: str | None) -> str:
    params: list[tuple[str, str]] = []
    if query:
        params.append(("q", query))
    if account:
        params.append(("account", account))
    return f"/api/v1/activity?{urlencode(params)}" if params else "/api/v1/activity"


def _activity_page_url(account: str | None) -> str:
    return f"/activity?{urlencode({'account': account})}" if account else "/activity"


def _validate_activity_filter_params(request: Request) -> None:
    for name in ("q", "account"):
        reject_control_char_query_param(request, name)
        reject_repeated_query_param(request, name)


def register_activity_routes(app: FastAPI, *, db_url: str, templates: Jinja2Templates) -> None:
    @app.get("/api/v1/activity", responses={200: ACTIVITY_RESPONSE_SCHEMA})
    def api_activity(
        request: Request,
        q: str | None = Query(None),
        account: str | None = Query(None),
    ) -> dict[str, Any]:
        _validate_activity_filter_params(request)
        with session_scope(db_url) as session:
            return activity_context(session, q, account)

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(
        request: Request,
        q: str | None = Query(None),
        account: str | None = Query(None),
    ) -> HTMLResponse:
        _validate_activity_filter_params(request)
        with session_scope(db_url) as session:
            context = activity_context(session, q, account)
        return templates.TemplateResponse(request, "activity.html", context)
