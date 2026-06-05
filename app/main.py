from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import auth as auth_module
from app.accounts import normalized_account, normalized_wallet_address, register_account_routes
from app.activity import register_activity_routes
from app.admin_routes import register_admin_routes
from app.bounty_api import register_bounty_api_routes
from app.bounty_attempts import (
    register_bounty_attempt_routes,
)
from app.config import get_settings
from app.db import create_schema, session_scope
from app.hub import is_ltc_lab_host, ltc_lab_context, mergework_hub_context
from app.json_payloads import json_object, optional_int, optional_str, required_int, required_str
from app.ledger.service import ensure_genesis, public_url_or_none
from app.ledger_views import ledger_entry_to_dict, recent_ledger_entries
from app.mcp import handle_mcp_request
from app.mcp_tools import call_mcp_tool
from app.me import me_page_context
from app.models import (
    Proof,
)
from app.path_params import (
    SQLITE_INTEGER_MAX,
    positive_bounty_id,
    positive_ledger_sequence,
    proof_hash_from_path,
)
from app.public_routes import register_public_routes
from app.query_validation import reject_noncanonical_int_query_param, reject_repeated_query_param
from app.status import health_status, system_status
from app.treasury_routes import register_treasury_routes
from app.wallet_api import register_wallet_api_routes
from app.webhooks.github import handle_github_webhook

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["safe_public_url"] = public_url_or_none

_oauth_configured = auth_module.oauth_configured
_safe_next_path = auth_module.safe_next_path
_signed_value = auth_module.signed_value
_verified_value = auth_module.verified_value

__all__ = [
    "_oauth_configured",
    "_safe_next_path",
    "_signed_value",
    "_verified_value",
    "create_app",
]

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
API_DOCS_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "connect-src 'self'; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
    "object-src 'none'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "worker-src 'self' blob:"
)
API_DOCS_PATHS = {"/api/docs", "/api/redoc"}


def _request_was_forwarded_https(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",", 1)[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _preserve_forwarded_https_redirect(request: Request, response: Response) -> None:
    if response.status_code not in {307, 308} or not _request_was_forwarded_https(request):
        return
    location = response.headers.get("location")
    if not location:
        return
    parsed = urlsplit(location)
    if parsed.scheme != "http" or parsed.netloc != request.url.netloc:
        return
    response.headers["location"] = urlunsplit(
        ("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment)
    )


def _csrf_token(action: str, login: str, secret: str) -> str:
    return _signed_value(f"{action}:{login}", secret)


def _verify_csrf_token(
    token: str | None, *, action: str, login: str, secret: str, max_age_seconds: int = 3_600
) -> bool:
    expected = f"{action}:{login}"
    return _verified_value(token, secret, max_age_seconds) == expected


def create_app(database_url: str | None = None, webhook_secret: str | None = None) -> FastAPI:
    settings = get_settings()
    db_url = database_url or settings.database_url
    secret = webhook_secret if webhook_secret is not None else settings.github_webhook_secret
    create_schema(db_url)
    with session_scope(db_url) as session:
        ensure_genesis(session)

    app = FastAPI(
        title="MergeWork",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )
    app.state.database_url = db_url
    app.state.webhook_secret = secret
    app.state.settings = settings

    def post_only_route() -> None:
        raise HTTPException(
            status_code=405,
            detail="Method Not Allowed",
            headers={"Allow": "POST"},
        )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next: Any) -> Any:
        original_method = request.scope["method"]
        if original_method == "HEAD":
            request.scope["method"] = "GET"
        try:
            response = await call_next(request)
        finally:
            request.scope["method"] = original_method
        if original_method == "HEAD":
            headers = dict(response.headers)
            headers["content-length"] = "0"
            response = Response(
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
            )
        if request.url.path in API_DOCS_PATHS:
            response.headers["Content-Security-Policy"] = API_DOCS_CSP
        _preserve_forwarded_https_redirect(request, response)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    static_dir = BASE_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    auth = auth_module.register_auth_routes(app, settings=settings)

    @app.get("/health")
    def health() -> dict[str, Any]:
        with session_scope(db_url) as session:
            return health_status(session)

    @app.get("/api/v1/status")
    def api_status() -> dict[str, Any]:
        with session_scope(db_url) as session:
            return system_status(session)

    _bounty_api = register_bounty_api_routes(
        app,
        db_url=db_url,
        require_admin_token=auth.require_admin_token,
        json_object=json_object,
        required_str=required_str,
        optional_str=optional_str,
        optional_int=optional_int,
        required_int=required_int,
        settings=settings,
    )
    list_bounties_by_status = _bounty_api["list_bounties_by_status"]
    api_bounty = _bounty_api["get_bounty_detail"]

    register_bounty_attempt_routes(
        app,
        db_url=db_url,
        require_github_login=auth.require_github_login,
        json_object=json_object,
        required_str=required_str,
        optional_int=optional_int,
        normalized_account=normalized_account,
        positive_bounty_id=positive_bounty_id,
        sqlite_integer_max=SQLITE_INTEGER_MAX,
    )

    register_treasury_routes(
        app,
        db_url=db_url,
        github_issue_token=settings.github_issue_token,
        public_base_url=settings.public_base_url,
        require_admin_token=auth.require_admin_token,
        require_github_login=auth.require_github_login,
        json_object=json_object,
    )

    register_account_routes(app, db_url=db_url, templates=templates)

    @app.get("/api/v1/auth/me")
    def api_auth_me(request: Request) -> dict[str, Any]:
        login = auth.github_login_from_request(request)
        return {"authenticated": login is not None, "github_login": login}

    register_wallet_api_routes(
        app,
        db_url=db_url,
        require_github_login=auth.require_github_login,
        json_object=json_object,
        required_str=required_str,
        required_int=required_int,
        optional_str=optional_str,
        normalized_wallet_address=normalized_wallet_address,
        post_only_route=post_only_route,
    )

    def ledger_rows(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with session_scope(db_url) as session:
            return recent_ledger_entries(session, limit, offset)

    @app.get("/api/v1/ledger")
    def api_ledger(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=SQLITE_INTEGER_MAX)] = 0,
    ) -> list[dict[str, Any]]:
        for name in ("limit", "offset"):
            reject_repeated_query_param(request, name)
            reject_noncanonical_int_query_param(request, name)
        return ledger_rows(limit, offset)

    @app.get("/api/v1/ledger/{sequence}")
    def api_ledger_entry(sequence: int | str) -> dict[str, Any]:
        sequence_id = positive_ledger_sequence(sequence)
        with session_scope(db_url) as session:
            entry = ledger_entry_to_dict(session, sequence_id)
            if entry is None:
                raise HTTPException(status_code=404, detail="ledger entry not found")
            return entry

    @app.get("/api/v1/proofs/{proof_hash}")
    def api_proof(proof_hash: str) -> dict[str, Any]:
        proof_hash = proof_hash_from_path(proof_hash)
        with session_scope(db_url) as session:
            proof = session.get(Proof, proof_hash)
            if proof is None:
                raise HTTPException(status_code=404, detail="proof not found")
            try:
                data = json.loads(proof.public_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=500, detail="invalid proof payload") from exc
            if not isinstance(data, dict):
                raise HTTPException(status_code=500, detail="invalid proof payload")
            return data

    register_activity_routes(app, db_url=db_url, templates=templates)

    @app.post("/webhooks/github")
    async def github_webhook(request: Request) -> JSONResponse:
        body = await request.body()
        headers = {key: value for key, value in request.headers.items()}
        normalized = {
            "X-GitHub-Delivery": headers.get("x-github-delivery", ""),
            "X-GitHub-Event": headers.get("x-github-event", ""),
            "X-Hub-Signature-256": headers.get("x-hub-signature-256", ""),
        }
        result = handle_github_webhook(
            db_url,
            normalized,
            body,
            secret,
            accepted_labelers=settings.github_accepted_labelers,
            github_issue_token=settings.github_issue_token,
        )
        code = 401 if result["status"] == "unauthorized" else 200
        return JSONResponse(result, status_code=code)

    @app.post("/mcp")
    async def mcp(request: Request) -> Any:
        return await handle_mcp_request(request, db_url, call_mcp_tool)

    @app.get("/", response_class=HTMLResponse)
    def hub(request: Request) -> HTMLResponse:
        if is_ltc_lab_host(request.headers.get("host", "")):
            return templates.TemplateResponse(
                request,
                "ltc_lab.html",
                ltc_lab_context(),
            )
        status_data = api_status()
        return templates.TemplateResponse(
            request,
            "hub.html",
            mergework_hub_context(status_data, settings.public_base_url),
        )

    register_public_routes(
        app,
        db_url=db_url,
        templates=templates,
        list_bounties_by_status=list_bounties_by_status,
        api_bounty=api_bounty,
        api_ledger=ledger_rows,
        api_ledger_entry=api_ledger_entry,
        api_proof=api_proof,
    )

    @app.get("/me", response_class=HTMLResponse)
    def me_page(request: Request) -> HTMLResponse:
        login = auth.github_login_from_request(request)
        with session_scope(db_url) as session:
            context = me_page_context(session, login)
        return templates.TemplateResponse(
            request,
            "me.html",
            context,
        )

    register_admin_routes(
        app,
        db_url=db_url,
        settings=settings,
        templates=templates,
        admin_login_from_request=auth.admin_login_from_request,
        require_admin=auth.require_admin,
        oauth_configured=_oauth_configured,
        csrf_token=_csrf_token,
        verify_csrf_token=_verify_csrf_token,
    )

    return app


app = create_app()
