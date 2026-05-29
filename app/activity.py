from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import session_scope
from app.serializers import activity_to_dict


def activity_context(session: Session, query: str | None = None) -> dict[str, Any]:
    return activity_to_dict(session, query)


def register_activity_routes(app: FastAPI, *, db_url: str, templates: Jinja2Templates) -> None:
    @app.get("/api/v1/activity")
    def api_activity(q: str | None = Query(None)) -> dict[str, Any]:
        with session_scope(db_url) as session:
            try:
                return activity_context(session, q)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request, q: str | None = Query(None)) -> HTMLResponse:
        with session_scope(db_url) as session:
            try:
                context = activity_context(session, q)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return templates.TemplateResponse(request, "activity.html", context)
