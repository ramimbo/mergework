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


def register_activity_routes(app: FastAPI, *, db_url: str, templates: Jinja2Templates) -> None:
    @app.get("/api/v1/activity")
    def api_activity(
        request: Request,
        q: str | None = Query(None),
        account: str | None = Query(None),
    ) -> dict[str, Any]:
        reject_control_char_query_param(request, "q")
        reject_repeated_query_param(request, "q")
        reject_control_char_query_param(request, "account")
        reject_repeated_query_param(request, "account")
        with session_scope(db_url) as session:
            return activity_context(session, q, account)

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(
        request: Request,
        q: str | None = Query(None),
        account: str | None = Query(None),
    ) -> HTMLResponse:
        reject_control_char_query_param(request, "q")
        reject_repeated_query_param(request, "q")
        reject_control_char_query_param(request, "account")
        reject_repeated_query_param(request, "account")
        with session_scope(db_url) as session:
            context = activity_context(session, q, account)
        return templates.TemplateResponse(request, "activity.html", context)
