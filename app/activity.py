from __future__ import annotations

from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import session_scope
from app.serializers import activity_to_dict


def activity_context(session: Session, query: str | None = None, offset: int = 0) -> dict[str, Any]:
    return activity_to_dict(session, query, offset=offset)


def register_activity_routes(app: FastAPI, *, db_url: str, templates: Jinja2Templates) -> None:
    @app.get("/api/v1/activity")
    def api_activity(
        q: str | None = Query(None),
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        with session_scope(db_url) as session:
            return activity_context(session, q, offset=offset)

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request, q: str | None = Query(None)) -> HTMLResponse:
        with session_scope(db_url) as session:
            context = activity_context(session, q)
        return templates.TemplateResponse(request, "activity.html", context)
