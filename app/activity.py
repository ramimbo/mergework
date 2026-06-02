from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.control_chars import contains_control_character
from app.db import session_scope
from app.query_validation import reject_control_char_query_param, reject_repeated_query_param
from app.serializers import activity_to_dict


def activity_context(session: Session, query: str | None = None) -> dict[str, Any]:
    if query is not None and contains_control_character(query):
        raise HTTPException(status_code=400, detail="q must not contain control characters")
    return activity_to_dict(session, query)


def register_activity_routes(app: FastAPI, *, db_url: str, templates: Jinja2Templates) -> None:
    @app.get("/api/v1/activity")
    def api_activity(request: Request, q: str | None = Query(None)) -> dict[str, Any]:
        reject_control_char_query_param(request, "q")
        reject_repeated_query_param(request, "q")
        with session_scope(db_url) as session:
            return activity_context(session, q)

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request, q: str | None = Query(None)) -> HTMLResponse:
        reject_control_char_query_param(request, "q")
        reject_repeated_query_param(request, "q")
        with session_scope(db_url) as session:
            context = activity_context(session, q)
        return templates.TemplateResponse(request, "activity.html", context)
