from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import session_scope
from app.serializers import activity_to_dict

ACTIVITY_LIMIT_DEFAULT = 100
ACTIVITY_LIMIT_MAX = 200


def _activity_api_url(query: str, limit: int) -> str:
    params: list[tuple[str, str]] = []
    if query:
        params.append(("q", query))
    if limit != ACTIVITY_LIMIT_DEFAULT:
        params.append(("limit", str(limit)))
    return f"/api/v1/activity?{urlencode(params)}" if params else "/api/v1/activity"


def activity_context(
    session: Session, query: str | None = None, limit: int = ACTIVITY_LIMIT_DEFAULT
) -> dict[str, Any]:
    return activity_to_dict(session, query, limit=limit)


def register_activity_routes(app: FastAPI, *, db_url: str, templates: Jinja2Templates) -> None:
    @app.get("/api/v1/activity")
    def api_activity(
        q: str | None = Query(None),
        limit: Annotated[int, Query(ge=1, le=ACTIVITY_LIMIT_MAX)] = ACTIVITY_LIMIT_DEFAULT,
    ) -> dict[str, Any]:
        with session_scope(db_url) as session:
            return activity_context(session, q, limit=limit)

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(
        request: Request,
        q: str | None = Query(None),
        limit: Annotated[int, Query(ge=1, le=ACTIVITY_LIMIT_MAX)] = ACTIVITY_LIMIT_DEFAULT,
    ) -> HTMLResponse:
        with session_scope(db_url) as session:
            context = activity_context(session, q, limit=limit)
        context["api_results_url"] = _activity_api_url(str(context["query"]), limit)
        return templates.TemplateResponse(request, "activity.html", context)
