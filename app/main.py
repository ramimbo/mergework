from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import create_schema, session_scope
from app.ledger.service import (
    GENESIS_SUPPLY_MICRO,
    TREASURY_ACCOUNT,
    LedgerError,
    create_bounty,
    ensure_genesis,
    format_mrwk,
    get_balance,
    link_wallet_to_github,
    pay_bounty,
    public_url_or_none,
    register_wallet,
    resolve_payout_account,
    submit_github_claim,
    submit_wallet_transfer,
)
from app.models import Account, Bounty, LedgerEntry, Proof, Wallet, WalletTransfer
from app.webhooks.github import handle_github_webhook

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["safe_public_url"] = public_url_or_none

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


def bounty_to_dict(bounty: Bounty) -> dict[str, Any]:
    return {
        "id": bounty.id,
        "repo": bounty.repo,
        "issue_number": bounty.issue_number,
        "issue_url": bounty.issue_url,
        "title": bounty.title,
        "reward_mrwk": format_mrwk(bounty.reward_microunits),
        "reserved_mrwk": format_mrwk(bounty.reserved_microunits),
        "status": bounty.status,
        "acceptance": bounty.acceptance,
        "created_at": bounty.created_at.isoformat(),
    }


def ledger_to_dict(entry: LedgerEntry, proof_hash: str | None = None) -> dict[str, Any]:
    return {
        "sequence": entry.sequence,
        "type": entry.entry_type,
        "from": entry.from_account,
        "to": entry.to_account,
        "amount_mrwk": format_mrwk(entry.amount_microunits),
        "reference": entry.reference,
        "previous_hash": entry.previous_hash,
        "entry_hash": entry.entry_hash,
        "proof_hash": proof_hash,
        "created_at": entry.created_at.isoformat(),
    }


def wallet_to_dict(session: Session, wallet: Wallet) -> dict[str, Any]:
    return {
        "address": wallet.address,
        "public_key_hex": wallet.public_key_hex,
        "label": wallet.label,
        "github_login": wallet.github_login,
        "balance_mrwk": format_mrwk(get_balance(session, wallet.address)),
        "nonce": wallet.nonce,
        "next_nonce": wallet.nonce + 1,
        "created_at": wallet.created_at.isoformat(),
    }


def wallet_transfer_to_dict(transfer: WalletTransfer) -> dict[str, Any]:
    return {
        "hash": transfer.hash,
        "type": "wallet_transfer",
        "ledger_sequence": transfer.ledger_sequence,
        "from_address": transfer.from_address,
        "to_address": transfer.to_address,
        "amount_mrwk": format_mrwk(transfer.amount_microunits),
        "nonce": transfer.nonce,
        "memo": transfer.memo,
        "created_at": transfer.created_at.isoformat(),
    }


def _host_without_port(request: Request) -> str:
    return request.headers.get("host", "").split(":", 1)[0].lower()


def _is_ltc_lab_host(request: Request) -> bool:
    return _host_without_port(request) in {"ltclab.site", "www.ltclab.site"}


def _proof_hashes_by_sequence(session: Session, sequences: list[int]) -> dict[int, str]:
    if not sequences:
        return {}
    rows = session.execute(
        select(Proof.ledger_sequence, Proof.hash).where(Proof.ledger_sequence.in_(sequences))
    ).all()
    return {int(sequence): str(proof_hash) for sequence, proof_hash in rows}


def _oauth_configured(settings: Settings) -> bool:
    return bool(
        settings.github_oauth_client_id
        and settings.github_oauth_client_secret
        and settings.cookie_secret
    )


def _safe_next_path(next_path: str | None) -> str:
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return "/me"
    return next_path


def _signed_value(value: str, secret: str) -> str:
    timestamp = str(int(time.time()))
    body = f"{value}|{timestamp}"
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}|{signature}"


def _verified_value(token: str | None, secret: str, max_age_seconds: int) -> str | None:
    if not token or not secret:
        return None
    try:
        value, timestamp, signature = token.rsplit("|", 2)
        age = int(time.time()) - int(timestamp)
    except ValueError:
        return None
    if age < 0 or age > max_age_seconds:
        return None
    expected = hmac.new(
        secret.encode(), f"{value}|{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return value


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid json body") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="json body must be an object")
    return data


def _required_str(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} is required")
    return value


def _optional_str(data: dict[str, Any], field: str, default: str = "") -> str:
    value = data.get(field, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    return value


def _required_int(data: dict[str, Any], field: str) -> int:
    value = data.get(field)
    if value is None or isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be an integer") from exc


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

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    static_dir = BASE_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def admin_login_from_request(request: Request) -> str | None:
        token = request.headers.get("x-mergework-admin-token", "")
        if settings.admin_token and hmac.compare_digest(token, settings.admin_token):
            return "api-token"
        login = _verified_value(request.cookies.get("mrwk_admin"), settings.cookie_secret, 86_400)
        if login and login.lower() in settings.admin_logins:
            return login.lower()
        return None

    def github_login_from_request(request: Request) -> str | None:
        login = _verified_value(request.cookies.get("mrwk_user"), settings.cookie_secret, 604_800)
        return login.lower() if login else None

    def require_github_login(request: Request) -> str:
        login = github_login_from_request(request)
        if login is None:
            raise HTTPException(status_code=401, detail="github login required")
        return login

    def require_admin(request: Request) -> str:
        login = admin_login_from_request(request)
        if login is None:
            raise HTTPException(status_code=401, detail="admin authentication required")
        return login

    def require_admin_token(request: Request) -> str:
        token = request.headers.get("x-mergework-admin-token", "")
        if settings.admin_token and hmac.compare_digest(token, settings.admin_token):
            return "api-token"
        raise HTTPException(status_code=401, detail="admin token required")

    @app.get("/health")
    def health() -> dict[str, Any]:
        with session_scope(db_url) as session:
            height = session.scalar(select(func.max(LedgerEntry.sequence))) or 0
        return {"ok": True, "service": "mergework", "ticker": "MRWK", "ledger_height": height}

    @app.get("/api/v1/status")
    def api_status() -> dict[str, Any]:
        with session_scope(db_url) as session:
            height = session.scalar(select(func.max(LedgerEntry.sequence))) or 0
            active = session.scalar(
                select(func.count()).select_from(Bounty).where(Bounty.status == "open")
            )
            treasury = get_balance(session, TREASURY_ACCOUNT)
        return {
            "name": "MergeWork",
            "ticker": "MRWK",
            "genesis_supply_mrwk": format_mrwk(GENESIS_SUPPLY_MICRO),
            "ledger_height": height,
            "active_bounties": active or 0,
            "treasury_balance_mrwk": format_mrwk(treasury),
            "future_path": "public snapshots, bridges, and onchain claims",
        }

    @app.get("/api/v1/bounties")
    def api_bounties() -> list[dict[str, Any]]:
        with session_scope(db_url) as session:
            bounties = session.scalars(select(Bounty).order_by(Bounty.id.desc())).all()
            return [bounty_to_dict(bounty) for bounty in bounties]

    @app.post("/api/v1/bounties")
    async def api_create_bounty(
        request: Request, admin_login: str = Depends(require_admin_token)
    ) -> dict[str, Any]:
        data = await request.json()
        with session_scope(db_url) as session:
            try:
                bounty = create_bounty(
                    session,
                    repo=data["repo"],
                    issue_number=int(data["issue_number"]),
                    issue_url=data["issue_url"],
                    title=data["title"],
                    reward_mrwk=str(data["reward_mrwk"]),
                    acceptance=data["acceptance"],
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            result = bounty_to_dict(bounty)
            result["created_by"] = admin_login
            return result

    @app.get("/api/v1/bounties/{bounty_id}")
    def api_bounty(bounty_id: int) -> dict[str, Any]:
        with session_scope(db_url) as session:
            bounty = session.get(Bounty, bounty_id)
            if bounty is None:
                raise HTTPException(status_code=404, detail="bounty not found")
            return bounty_to_dict(bounty)

    @app.post("/api/v1/bounties/{bounty_id}/pay")
    async def api_pay_bounty(
        bounty_id: int,
        request: Request,
        admin_login: str = Depends(require_admin_token),
    ) -> dict[str, Any]:
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="json body must be an object")
        try:
            requested_account = str(data["to_account"])
            submission_url = str(data["submission_url"])
        except KeyError as exc:
            raise HTTPException(
                status_code=400, detail=f"missing required field: {exc.args[0]}"
            ) from exc
        accepted_by = str(data.get("accepted_by") or admin_login)
        verifier_result = {
            "source": "admin_api",
            "accepted_by": accepted_by,
        }
        if data.get("note") is not None:
            verifier_result["note"] = str(data["note"])[:240]
        with session_scope(db_url) as session:
            try:
                to_account = resolve_payout_account(session, requested_account)
                proof = pay_bounty(
                    session,
                    bounty_id=bounty_id,
                    to_account=to_account,
                    submission_url=submission_url,
                    accepted_by=accepted_by,
                    verifier_result=verifier_result,
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {
                "status": "paid",
                "bounty_id": bounty_id,
                "to_account": to_account,
                "proof_hash": proof.hash,
            }

    @app.get("/api/v1/accounts/{account:path}")
    def api_account(account: str) -> dict[str, Any]:
        with session_scope(db_url) as session:
            account_row = session.get(Account, account)
            return {
                "account": account,
                "ledger_address": account,
                "exists": account_row is not None,
                "balance_mrwk": format_mrwk(get_balance(session, account)),
                "transfer_status": (
                    "MRWK wallet transfers are enabled for registered mrwk1 addresses."
                ),
            }

    @app.get("/api/v1/auth/me")
    def api_auth_me(request: Request) -> dict[str, Any]:
        login = github_login_from_request(request)
        return {"authenticated": login is not None, "github_login": login}

    @app.post("/api/v1/wallets/register")
    async def api_register_wallet(request: Request) -> dict[str, Any]:
        data = await _json_object(request)
        with session_scope(db_url) as session:
            try:
                wallet = register_wallet(
                    session,
                    public_key_hex=_required_str(data, "public_key_hex"),
                    label=_optional_str(data, "label") if data.get("label") is not None else None,
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return wallet_to_dict(session, wallet)

    @app.get("/api/v1/wallets/{address}")
    def api_wallet(address: str) -> dict[str, Any]:
        with session_scope(db_url) as session:
            wallet = session.get(Wallet, address.lower())
            if wallet is None:
                raise HTTPException(status_code=404, detail="wallet not found")
            return wallet_to_dict(session, wallet)

    @app.post("/api/v1/wallets/link-github")
    async def api_link_wallet_github(
        request: Request, github_login: str = Depends(require_github_login)
    ) -> dict[str, Any]:
        data = await _json_object(request)
        with session_scope(db_url) as session:
            try:
                wallet = link_wallet_to_github(
                    session,
                    address=_required_str(data, "address"),
                    github_login=github_login,
                    nonce=_required_int(data, "nonce"),
                    signature_hex=_required_str(data, "signature_hex"),
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return wallet_to_dict(session, wallet)

    @app.post("/api/v1/github/claim")
    async def api_github_claim(
        request: Request, github_login: str = Depends(require_github_login)
    ) -> dict[str, Any]:
        data = await _json_object(request)
        with session_scope(db_url) as session:
            try:
                entry = submit_github_claim(
                    session,
                    address=_required_str(data, "address"),
                    github_login=github_login,
                    nonce=_required_int(data, "nonce"),
                    signature_hex=_required_str(data, "signature_hex"),
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return ledger_to_dict(entry)

    @app.post("/api/v1/transfers")
    async def api_submit_transfer(request: Request) -> dict[str, Any]:
        data = await _json_object(request)
        with session_scope(db_url) as session:
            try:
                transfer = submit_wallet_transfer(
                    session,
                    from_address=_required_str(data, "from_address"),
                    to_address=_required_str(data, "to_address"),
                    amount_mrwk=_required_str(data, "amount_mrwk"),
                    nonce=_required_int(data, "nonce"),
                    memo=_optional_str(data, "memo"),
                    signature_hex=_required_str(data, "signature_hex"),
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return wallet_transfer_to_dict(transfer)

    @app.get("/api/v1/ledger")
    def api_ledger(limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        with session_scope(db_url) as session:
            entries = session.scalars(
                select(LedgerEntry).order_by(LedgerEntry.sequence.desc()).limit(limit)
            ).all()
            proofs = _proof_hashes_by_sequence(session, [entry.sequence for entry in entries])
            return [ledger_to_dict(entry, proofs.get(entry.sequence)) for entry in entries]

    @app.get("/api/v1/ledger/{sequence}")
    def api_ledger_entry(sequence: int) -> dict[str, Any]:
        with session_scope(db_url) as session:
            entry = session.get(LedgerEntry, sequence)
            if entry is None:
                raise HTTPException(status_code=404, detail="ledger entry not found")
            proof = session.scalar(select(Proof).where(Proof.ledger_sequence == sequence).limit(1))
            return ledger_to_dict(entry, proof.hash if proof else None)

    @app.get("/api/v1/proofs/{proof_hash}")
    def api_proof(proof_hash: str) -> dict[str, Any]:
        with session_scope(db_url) as session:
            proof = session.get(Proof, proof_hash)
            if proof is None:
                raise HTTPException(status_code=404, detail="proof not found")
            data = json.loads(proof.public_json)
            if not isinstance(data, dict):
                raise HTTPException(status_code=500, detail="invalid proof payload")
            return data

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
            db_url, normalized, body, secret, settings.github_accepted_labelers
        )
        code = 401 if result["status"] == "unauthorized" else 200
        return JSONResponse(result, status_code=code)

    @app.post("/mcp")
    async def mcp(request: Request) -> dict[str, Any]:
        payload = await request.json()
        response_id = payload.get("id")
        method = payload.get("method")
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": response_id,
                "result": {
                    "tools": [
                        {"name": "list_bounties", "description": "List open MRWK bounties"},
                        {"name": "get_bounty", "description": "Get a bounty by id"},
                        {"name": "get_balance", "description": "Get an account balance"},
                        {
                            "name": "register_wallet",
                            "description": "Register an MRWK wallet public key",
                        },
                        {"name": "get_wallet", "description": "Get an MRWK wallet by address"},
                        {
                            "name": "submit_wallet_transfer",
                            "description": "Submit a signed MRWK wallet transfer",
                        },
                        {"name": "get_ledger_entry", "description": "Get a ledger entry"},
                        {
                            "name": "submit_work_proof",
                            "description": "Return submission instructions",
                        },
                    ]
                },
            }
        if method != "tools/call":
            return {
                "jsonrpc": "2.0",
                "id": response_id,
                "error": {"code": -32601, "message": "unknown method"},
            }
        params = payload.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        if not isinstance(name, str):
            return {
                "jsonrpc": "2.0",
                "id": response_id,
                "error": {"code": -32602, "message": "tool name is required"},
            }
        try:
            text = _call_mcp_tool(db_url, name, args)
        except (KeyError, TypeError, ValueError, LedgerError):
            return {
                "jsonrpc": "2.0",
                "id": response_id,
                "error": {"code": -32602, "message": "invalid tool arguments"},
            }
        return {
            "jsonrpc": "2.0",
            "id": response_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }

    @app.get("/", response_class=HTMLResponse)
    def hub(request: Request) -> HTMLResponse:
        if _is_ltc_lab_host(request):
            return templates.TemplateResponse(
                request,
                "ltc_lab.html",
                {
                    "site_context": "ltc_lab",
                    "projects": [
                        {
                            "name": "MergeWork",
                            "tagline": "MRWK from LTC Lab",
                            "href": "https://mrwk.ltclab.site",
                            "status": "live",
                        },
                        {
                            "name": "MergeWork API",
                            "tagline": "Public MRWK status, bounty, ledger, and proof endpoints",
                            "href": "https://api.mrwk.ltclab.site",
                            "status": "live",
                        },
                        {
                            "name": "MergeWork MCP",
                            "tagline": "Tool endpoint for bounty and ledger queries",
                            "href": "https://mcp.mrwk.ltclab.site",
                            "status": "live",
                        },
                    ],
                },
            )
        status_data = api_status()
        return templates.TemplateResponse(
            request,
            "hub.html",
            {
                "status": status_data,
                "public_base_url": settings.public_base_url,
            },
        )

    @app.get("/bounties", response_class=HTMLResponse)
    def bounties_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "bounties.html", {"bounties": api_bounties()})

    @app.get("/bounties/{bounty_id}", response_class=HTMLResponse)
    def bounty_page(request: Request, bounty_id: int) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "bounty_detail.html", {"bounty": api_bounty(bounty_id)}
        )

    @app.get("/ledger", response_class=HTMLResponse)
    def ledger_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "ledger.html", {"entries": api_ledger()})

    @app.get("/ledger/{sequence}", response_class=HTMLResponse)
    def ledger_entry_page(request: Request, sequence: int) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "ledger_entry.html", {"entry": api_ledger_entry(sequence)}
        )

    @app.get("/accounts/{account:path}", response_class=HTMLResponse)
    def account_page(request: Request, account: str) -> HTMLResponse:
        with session_scope(db_url) as session:
            account_data = api_account(account)
            entries = session.scalars(
                select(LedgerEntry)
                .where(or_(LedgerEntry.from_account == account, LedgerEntry.to_account == account))
                .order_by(LedgerEntry.sequence.desc())
                .limit(100)
            ).all()
            proofs = _proof_hashes_by_sequence(session, [entry.sequence for entry in entries])
            transactions = [ledger_to_dict(entry, proofs.get(entry.sequence)) for entry in entries]
        return templates.TemplateResponse(
            request, "account.html", {"account": account_data, "transactions": transactions}
        )

    @app.get("/wallets", response_class=HTMLResponse)
    def wallets_page(request: Request) -> HTMLResponse:
        with session_scope(db_url) as session:
            wallets = session.scalars(
                select(Wallet).order_by(Wallet.created_at.desc()).limit(100)
            ).all()
            wallet_rows = [wallet_to_dict(session, wallet) for wallet in wallets]
        return templates.TemplateResponse(request, "wallets.html", {"wallets": wallet_rows})

    @app.get("/wallets/{address}", response_class=HTMLResponse)
    def wallet_page(request: Request, address: str) -> HTMLResponse:
        with session_scope(db_url) as session:
            wallet = session.get(Wallet, address.lower())
            if wallet is None:
                raise HTTPException(status_code=404, detail="wallet not found")
            wallet_data = wallet_to_dict(session, wallet)
            entries = session.scalars(
                select(LedgerEntry)
                .where(
                    or_(
                        LedgerEntry.from_account == wallet.address,
                        LedgerEntry.to_account == wallet.address,
                    )
                )
                .order_by(LedgerEntry.sequence.desc())
                .limit(100)
            ).all()
            proofs = _proof_hashes_by_sequence(session, [entry.sequence for entry in entries])
            transactions = [ledger_to_dict(entry, proofs.get(entry.sequence)) for entry in entries]
        return templates.TemplateResponse(
            request,
            "wallet_detail.html",
            {"wallet": wallet_data, "transactions": transactions},
        )

    @app.get("/transfer", response_class=HTMLResponse)
    def transfer_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "transfer.html")

    @app.get("/proofs/{proof_hash}", response_class=HTMLResponse)
    def proof_page(request: Request, proof_hash: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "proof.html", {"proof": api_proof(proof_hash), "proof_hash": proof_hash}
        )

    @app.get("/docs", response_class=HTMLResponse)
    def docs_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "docs.html")

    @app.get("/auth/github/login")
    def auth_github_login(next_path: str | None = Query(None, alias="next")) -> RedirectResponse:
        if not _oauth_configured(settings):
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        safe_next = _safe_next_path(next_path)
        state_value = f"{secrets.token_urlsafe(24)},{safe_next}"
        state = _signed_value(state_value, settings.cookie_secret)
        query = urlencode(
            {
                "client_id": settings.github_oauth_client_id,
                "redirect_uri": f"{settings.public_base_url}/auth/github/callback",
                "scope": "read:user",
                "state": state,
            }
        )
        response = RedirectResponse(
            f"https://github.com/login/oauth/authorize?{query}", status_code=302
        )
        response.set_cookie(
            "mrwk_oauth_state", state, httponly=True, secure=True, samesite="lax", max_age=600
        )
        return response

    @app.get("/auth/github/callback")
    async def auth_github_callback(request: Request, code: str, state: str) -> RedirectResponse:
        if not _oauth_configured(settings):
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        cookie_state = request.cookies.get("mrwk_oauth_state")
        if not cookie_state or not hmac.compare_digest(cookie_state, state):
            raise HTTPException(status_code=401, detail="invalid OAuth state")
        state_value = _verified_value(state, settings.cookie_secret, 600)
        if state_value is None:
            raise HTTPException(status_code=401, detail="expired OAuth state")
        try:
            _, next_path = state_value.split(",", 1)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="invalid OAuth state") from exc
        next_path = _safe_next_path(next_path)
        async with httpx.AsyncClient(timeout=10) as client:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.github_oauth_client_id,
                    "client_secret": settings.github_oauth_client_secret,
                    "code": code,
                    "redirect_uri": f"{settings.public_base_url}/auth/github/callback",
                },
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not access_token:
                raise HTTPException(status_code=401, detail="GitHub OAuth token exchange failed")
            user_response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                },
            )
            user_response.raise_for_status()
            login = str(user_response.json().get("login", "")).lower()
            if not login:
                raise HTTPException(status_code=401, detail="GitHub OAuth user lookup failed")
        response = RedirectResponse(next_path, status_code=302)
        response.set_cookie(
            "mrwk_user",
            _signed_value(login, settings.cookie_secret),
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=604_800,
        )
        if login in settings.admin_logins:
            response.set_cookie(
                "mrwk_admin",
                _signed_value(login, settings.cookie_secret),
                httponly=True,
                secure=True,
                samesite="lax",
                max_age=86_400,
            )
        response.delete_cookie("mrwk_oauth_state")
        return response

    @app.get("/admin/login")
    def admin_login() -> RedirectResponse:
        return RedirectResponse("/auth/github/login?next=/admin", status_code=302)

    @app.get("/admin/callback")
    async def admin_callback(request: Request) -> RedirectResponse:
        suffix = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/auth/github/callback{suffix}", status_code=302)

    @app.post("/auth/logout")
    def auth_logout() -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("mrwk_user")
        response.delete_cookie("mrwk_admin")
        return response

    @app.get("/me", response_class=HTMLResponse)
    def me_page(request: Request) -> HTMLResponse:
        login = github_login_from_request(request)
        return templates.TemplateResponse(request, "me.html", {"github_login": login})

    @app.post("/admin/logout")
    def admin_logout() -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("mrwk_admin")
        response.delete_cookie("mrwk_user")
        return response

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request) -> Any:
        login = admin_login_from_request(request)
        if login is None:
            if _oauth_configured(settings):
                return RedirectResponse("/auth/github/login?next=/admin", status_code=302)
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        return templates.TemplateResponse(
            request,
            "admin.html",
            {
                "login": login,
                "csrf_token": _csrf_token("admin-bounty", login, settings.cookie_secret),
            },
        )

    @app.post("/admin/bounties")
    def admin_create_bounty(
        request: Request,
        repo: str = Form(...),
        issue_number: int = Form(...),
        issue_url: str = Form(...),
        title: str = Form(...),
        reward_mrwk: str = Form(...),
        acceptance: str = Form(...),
        csrf_token: str | None = Form(None),
        admin_login: str = Depends(require_admin),
    ) -> RedirectResponse:
        del request
        if admin_login != "api-token" and not _verify_csrf_token(
            csrf_token,
            action="admin-bounty",
            login=admin_login,
            secret=settings.cookie_secret,
        ):
            raise HTTPException(status_code=403, detail="invalid CSRF token")
        with session_scope(db_url) as session:
            try:
                bounty = create_bounty(
                    session,
                    repo=repo,
                    issue_number=issue_number,
                    issue_url=issue_url,
                    title=title,
                    reward_mrwk=reward_mrwk,
                    acceptance=acceptance,
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            bounty_id = bounty.id
        return RedirectResponse(f"/bounties/{bounty_id}", status_code=303)

    return app


def _call_mcp_tool(database_url: str, name: str, args: dict[str, Any]) -> str:
    with session_scope(database_url) as session:
        if name == "list_bounties":
            bounties = session.scalars(
                select(Bounty).where(Bounty.status == "open").order_by(Bounty.id.desc()).limit(25)
            ).all()
            return json.dumps([bounty_to_dict(bounty) for bounty in bounties])
        if name == "get_bounty":
            bounty = session.get(Bounty, int(args["id"]))
            if bounty is None:
                return "bounty not found"
            return json.dumps(bounty_to_dict(bounty))
        if name == "get_balance":
            account = str(args["account"])
            return f"{account}: {format_mrwk(get_balance(session, account))} MRWK"
        if name == "register_wallet":
            wallet = register_wallet(
                session,
                public_key_hex=str(args["public_key_hex"]),
                label=str(args["label"]) if args.get("label") is not None else None,
            )
            return json.dumps(wallet_to_dict(session, wallet))
        if name == "get_wallet":
            wallet_row = session.get(Wallet, str(args["address"]).lower())
            if wallet_row is None:
                return "wallet not found"
            return json.dumps(wallet_to_dict(session, wallet_row))
        if name == "submit_wallet_transfer":
            transfer = submit_wallet_transfer(
                session,
                from_address=str(args["from_address"]),
                to_address=str(args["to_address"]),
                amount_mrwk=str(args["amount_mrwk"]),
                nonce=int(args["nonce"]),
                memo=str(args.get("memo", "")),
                signature_hex=str(args["signature_hex"]),
            )
            return json.dumps(wallet_transfer_to_dict(transfer))
        if name == "get_ledger_entry":
            entry = session.get(LedgerEntry, int(args["sequence"]))
            if entry is None:
                return "ledger entry not found"
            return json.dumps(ledger_to_dict(entry))
        if name == "submit_work_proof":
            return (
                "Open a focused PR or issue, reference the MRWK bounty, include test evidence, "
                "and wait for a maintainer to apply mrwk:accepted."
            )
    return "unknown tool"


app = create_app()
