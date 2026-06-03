from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.admin import (
    ADMIN_WEBHOOK_LIMIT_OPTIONS,
    admin_page_context,
    create_admin_bounty_from_form,
)
from app.config import Settings
from app.db import session_scope
from app.ledger.service import LedgerError
from app.query_validation import reject_noncanonical_int_query_param, reject_repeated_query_param


def register_admin_routes(
    app: FastAPI,
    *,
    db_url: str,
    settings: Settings,
    templates: Jinja2Templates,
    admin_login_from_request: Callable[[Request], str | None],
    require_admin: Callable[[Request], str],
    oauth_configured: Callable[[Settings], bool],
    csrf_token: Callable[[str, str, str], str],
    verify_csrf_token: Callable[..., bool],
) -> None:
    @app.get("/admin/login")
    def admin_login() -> RedirectResponse:
        return RedirectResponse("/auth/github/login?next=/admin", status_code=302)

    @app.get("/admin/callback")
    async def admin_callback(request: Request) -> RedirectResponse:
        suffix = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/auth/github/callback{suffix}", status_code=302)

    @app.post("/admin/logout")
    def admin_logout() -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("mrwk_admin")
        response.delete_cookie("mrwk_user")
        return response

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(
        request: Request,
        webhook_status: str | None = Query(None),
        webhook_limit: Annotated[int, Query(ge=1, le=max(ADMIN_WEBHOOK_LIMIT_OPTIONS))] = 25,
        proposal_id: Annotated[int | None, Query(ge=1)] = None,
    ) -> Any:
        reject_repeated_query_param(request, "webhook_status")
        reject_repeated_query_param(request, "webhook_limit")
        reject_repeated_query_param(request, "proposal_id")
        reject_noncanonical_int_query_param(request, "webhook_limit")
        reject_noncanonical_int_query_param(request, "proposal_id")
        login = admin_login_from_request(request)
        if login is None:
            if oauth_configured(settings):
                return RedirectResponse("/auth/github/login?next=/admin", status_code=302)
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        with session_scope(db_url) as session:
            try:
                context = admin_page_context(
                    session,
                    login=login,
                    csrf_token=csrf_token("admin-bounty", login, settings.cookie_secret),
                    webhook_status=webhook_status,
                    webhook_limit=webhook_limit,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            context["proposal_id"] = proposal_id
        return templates.TemplateResponse(
            request,
            "admin.html",
            context,
        )

    @app.post("/admin/bounties")
    def admin_create_bounty(
        repo: str = Form(...),
        issue_number: int = Form(...),
        issue_url: str = Form(...),
        title: str = Form(...),
        reward_mrwk: str = Form(...),
        max_awards: int = Form(1),
        acceptance: str = Form(...),
        csrf_token_value: str | None = Form(None, alias="csrf_token"),
        admin_login: str = Depends(require_admin),
    ) -> RedirectResponse:
        if admin_login != "api-token" and not verify_csrf_token(
            csrf_token_value,
            action="admin-bounty",
            login=admin_login,
            secret=settings.cookie_secret,
        ):
            raise HTTPException(status_code=403, detail="invalid CSRF token")
        with session_scope(db_url) as session:
            try:
                proposal_id = create_admin_bounty_from_form(
                    session,
                    repo=repo,
                    issue_number=issue_number,
                    issue_url=issue_url,
                    title=title,
                    reward_mrwk=reward_mrwk,
                    max_awards=max_awards,
                    acceptance=acceptance,
                    proposed_by=admin_login,
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(f"/admin?proposal_id={proposal_id}", status_code=303)
