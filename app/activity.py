from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.accounts import normalized_account
from app.activity_sorting import (
    ACTIVITY_SORT_ERROR,
    ACTIVITY_SORT_LABELS,
    normalize_activity_sort,
)
from app.control_chars import contains_control_character
from app.db import session_scope
from app.query_validation import reject_control_char_query_param, reject_repeated_query_param
from app.serializers import activity_to_dict


def _activity_sort_error(value_error: ValueError) -> HTTPException:
    detail = str(value_error)
    allowed_details = {
        ACTIVITY_SORT_ERROR,
        "sort must not contain control characters",
    }
    if detail not in allowed_details:
        detail = ACTIVITY_SORT_ERROR
    return HTTPException(status_code=400, detail=detail)


def activity_context(
    session: Session,
    query: str | None = None,
    account: str | None = None,
    sort: str | None = None,
) -> dict[str, Any]:
    if query is not None and contains_control_character(query):
        raise HTTPException(status_code=400, detail="q must not contain control characters")
    normalized = normalized_account(account) if account is not None else None
    try:
        normalized_sort = normalize_activity_sort(sort)
    except ValueError as exc:
        raise _activity_sort_error(exc) from exc
    context = activity_to_dict(session, query, account=normalized, sort=normalized_sort)
    context["api_activity_url"] = _activity_api_url(
        context["query"], context.get("account"), normalized_sort
    )
    context["clear_activity_url"] = _activity_page_url(context.get("account"))
    context["account_page_url"] = _account_page_url(context.get("account"))
    context["sort_options"] = ACTIVITY_SORT_LABELS
    context["selected_sort"] = normalized_sort
    context["sort_urls"] = {
        value: _activity_page_url(context.get("account"), context["query"], value)
        for value in ACTIVITY_SORT_LABELS
    }
    return context


def _account_page_url(account: str | None) -> str | None:
    return f"/accounts/{quote(account, safe=':')}" if account else None


def _activity_api_url(query: str, account: str | None, sort: str) -> str:
    params: list[tuple[str, str]] = []
    if query:
        params.append(("q", query))
    if account:
        params.append(("account", account))
    if sort and sort != "mrwk":
        params.append(("sort", sort))
    return f"/api/v1/activity?{urlencode(params)}" if params else "/api/v1/activity"


def _activity_page_url(
    account: str | None, query: str | None = None, sort: str | None = None
) -> str:
    params: list[tuple[str, str]] = []
    if account:
        params.append(("account", account))
    if query:
        params.append(("q", query))
    if sort and sort != "mrwk":
        params.append(("sort", sort))
    return f"/activity?{urlencode(params)}" if params else "/activity"


def _validate_activity_filter_params(request: Request) -> None:
    for name in ("q", "account", "sort"):
        reject_control_char_query_param(request, name)
        reject_repeated_query_param(request, name)


def register_activity_routes(app: FastAPI, *, db_url: str, templates: Jinja2Templates) -> None:
    @app.get("/api/v1/activity")
    def api_activity(
        request: Request,
        q: str | None = Query(None),
        account: str | None = Query(None),
        sort: str | None = Query(None),
    ) -> dict[str, Any]:
        _validate_activity_filter_params(request)
        try:
            with session_scope(db_url) as session:
                return activity_context(session, q, account, sort)
        except ValueError as exc:
            raise _activity_sort_error(exc) from exc

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(
        request: Request,
        q: str | None = Query(None),
        account: str | None = Query(None),
        sort: str | None = Query(None),
    ) -> HTMLResponse:
        _validate_activity_filter_params(request)
        try:
            with session_scope(db_url) as session:
                context = activity_context(session, q, account, sort)
        except ValueError as exc:
            raise _activity_sort_error(exc) from exc
        return templates.TemplateResponse(request, "activity.html", context)
