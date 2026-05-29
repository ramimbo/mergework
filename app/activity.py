from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import session_scope
from app.serializers import activity_to_dict


def _normalized_activity_search_query(query: str | None) -> str | None:
    if query is None:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in query):
        raise HTTPException(status_code=400, detail="q must not contain control characters")
    return query.strip()


def activity_context(session: Session, query: str | None = None) -> dict[str, Any]:
    return activity_to_dict(session, _normalized_activity_search_query(query))


def register_activity_routes(app: FastAPI, *, db_url: str, templates: Jinja2Templates) -> None:
    @app.get("/api/v1/activity")
    def api_activity(q: str | None = Query(None)) -> dict[str, Any]:
        with session_scope(db_url) as session:
            return activity_context(session, q)

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request, q: str | None = Query(None)) -> HTMLResponse:
        with session_scope(db_url) as session:
            context = activity_context(session, q)
        return templates.TemplateResponse(request, "activity.html", context)
