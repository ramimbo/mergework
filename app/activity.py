from __future__ import annotations

from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.accounts import normalized_account
from app.control_chars import contains_control_character
from app.db import session_scope
from app.query_validation import (
    reject_control_char_query_param,
    reject_noncanonical_int_query_param,
    reject_repeated_query_param,
)
from app.serializers import activity_to_dict


def activity_context(
    session: Session,
    query: str | None = None,
    account: str | None = None,
    *,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    if query is not None and contains_control_character(query):
        raise HTTPException(status_code=400, detail="q must not contain control characters")
    normalized = normalized_account(account) if account is not None else None
    return activity_to_dict(session, query, account=normalized, limit=limit, offset=offset)


def register_activity_routes(app: FastAPI, *, db_url: str, templates: Jinja2Templates) -> None:
    @app.get("/api/v1/activity")
    def api_activity(
        request: Request,
        q: str | None = Query(None),
        account: str | None = Query(None),
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ) -> dict[str, Any]:
        reject_control_char_query_param(request, "q")
        reject_repeated_query_param(request, "q")
        reject_control_char_query_param(request, "account")
        reject_repeated_query_param(request, "account")
        reject_control_char_query_param(request, "limit")
        reject_repeated_query_param(request, "limit")
        reject_noncanonical_int_query_param(request, "limit")
        reject_control_char_query_param(request, "offset")
        reject_repeated_query_param(request, "offset")
        reject_noncanonical_int_query_param(request, "offset")
        with session_scope(db_url) as session:
            return activity_context(session, q, account, limit=limit, offset=offset)

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request, q: str | None = Query(None)) -> HTMLResponse:
        reject_control_char_query_param(request, "q")
        reject_repeated_query_param(request, "q")
        with session_scope(db_url) as session:
            context = activity_context(session, q)
        return templates.TemplateResponse(request, "activity.html", context)
