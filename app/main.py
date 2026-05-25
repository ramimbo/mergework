from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import create_schema, session_scope
from app.ledger.reconciliation import payout_reconciliation_summary, reconcile_accepted_payouts
from app.ledger.service import (
    GENESIS_SUPPLY_MICRO,
    TREASURY_ACCOUNT,
    LedgerError,
    close_bounty,
    create_bounty,
    ensure_genesis,
    format_mrwk,
    get_balance,
    link_wallet_to_github,
    linked_wallet_for_github,
    pay_bounty,
    public_url_or_none,
    register_wallet,
    resolve_payout_account,
    submit_github_claim,
    submit_wallet_transfer,
    validate_public_url,
)
from app.mcp import handle_mcp_request
from app.models import (
    Account,
    Bounty,
    BountyAttempt,
    LedgerEntry,
    Proof,
    Submission,
    Wallet,
    WebhookEvent,
)
from app.serializers import (
    accepted_work_for_account,
    account_accepted_summary,
    activity_to_dict,
    bounty_awards_to_dict,
    bounty_list_summary,
    bounty_payment_payload,
    bounty_to_dict,
    ledger_to_dict,
    payout_reconciliation_to_dict,
    proof_public_payload,
    safe_accepted_work_for_account,
    safe_account_accepted_summary,
    wallet_to_dict,
    wallet_transfer_to_dict,
)
from app.wallets import WalletError, normalize_wallet_address
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
GITHUB_LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")
HEX_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
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
SQLITE_INTEGER_MAX = 2**63 - 1
DEFAULT_ATTEMPT_TTL_SECONDS = 24 * 60 * 60
MIN_ATTEMPT_TTL_SECONDS = 60
MAX_ATTEMPT_TTL_SECONDS = 7 * 24 * 60 * 60


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


def _issue_number_search_value(query: str) -> int | None:
    if not query.isdigit():
        return None
    try:
        issue_number = int(query)
    except ValueError:
        return None
    return issue_number if issue_number <= SQLITE_INTEGER_MAX else None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _attempt_effective_status(attempt: BountyAttempt, now: datetime) -> str:
    if attempt.status == "active" and _as_utc(attempt.expires_at) <= now:
        return "expired"
    return attempt.status


def bounty_attempt_to_dict(attempt: BountyAttempt, now: datetime | None = None) -> dict[str, Any]:
    now = now or _utc_now()
    return {
        "id": attempt.id,
        "bounty_id": attempt.bounty_id,
        "submitter_account": attempt.submitter_account,
        "source_url": attempt.source_url,
        "status": _attempt_effective_status(attempt, now),
        "expires_at": _as_utc(attempt.expires_at).isoformat(),
        "created_at": _as_utc(attempt.created_at).isoformat(),
        "updated_at": _as_utc(attempt.updated_at).isoformat(),
    }


def _active_attempt_conditions(bounty_id: int, now: datetime) -> tuple[Any, ...]:
    return (
        BountyAttempt.bounty_id == bounty_id,
        BountyAttempt.status == "active",
        BountyAttempt.expires_at > now,
    )


def bounty_attempt_warnings(session: Session, bounty: Bounty, now: datetime) -> list[str]:
    warnings: list[str] = []
    awards_remaining = max(0, bounty.max_awards - bounty.awards_paid)
    if bounty.status != "open":
        warnings.append(f"bounty is {bounty.status}")
        awards_remaining = 0
    if awards_remaining <= 0:
        warnings.append("bounty has no award slots remaining")
    active_count = session.scalar(
        select(func.count())
        .select_from(BountyAttempt)
        .where(*_active_attempt_conditions(bounty.id, now))
    )
    if active_count and active_count > 1:
        warnings.append(f"bounty has {active_count} active attempts")
    return warnings


def expire_stale_bounty_attempts(
    session: Session, bounty_id: int, now: datetime, submitter_account: str | None = None
) -> None:
    query = update(BountyAttempt).where(
        BountyAttempt.bounty_id == bounty_id,
        BountyAttempt.status == "active",
        BountyAttempt.expires_at <= now,
    )
    if submitter_account is not None:
        query = query.where(BountyAttempt.submitter_account == submitter_account)
    session.execute(query.values(status="expired", updated_at=now))


def _payout_response_from_proof(proof: Proof, *, status: str) -> dict[str, Any]:
    data = bounty_payment_payload(proof)
    if data is None:
        raise HTTPException(status_code=500, detail="invalid proof payload")
    return {
        "status": status,
        "bounty_id": proof.bounty_id,
        "to_account": data.get("to_account"),
        "submission_id": proof.submission_id,
        "submission_url": data.get("submission_url"),
        "ledger_sequence": proof.ledger_sequence,
        "ledger_url": f"/ledger/{proof.ledger_sequence}",
        "proof_hash": proof.hash,
        "proof_url": f"/proofs/{proof.hash}",
    }


def _existing_payout_proof_for_submission(
    session: Session, bounty_id: int, submission_url: str
) -> Proof | None:
    submission = session.scalar(
        select(Submission)
        .where(Submission.bounty_id == bounty_id, Submission.url == submission_url)
        .limit(1)
    )
    if submission is None:
        return None
    return session.scalar(
        select(Proof)
        .where(Proof.submission_id == submission.id, Proof.kind == "bounty_payment")
        .limit(1)
    )


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
    if (
        not next_path
        or not next_path.startswith("/")
        or next_path.startswith("//")
        or len(next_path) > 2048
        or "\\" in next_path
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in next_path)
    ):
        return "/me"
    return next_path


def _normalized_account(account: str) -> str:
    if not account or not account.strip():
        raise HTTPException(status_code=400, detail="account must not be empty")
    if re.search(r"[\x00-\x1f\x7f]", account):
        raise HTTPException(status_code=400, detail="account must not contain control characters")
    clean = account.strip()
    lower = clean.lower()
    if lower == TREASURY_ACCOUNT:
        return TREASURY_ACCOUNT
    if lower.startswith("treasury:"):
        raise HTTPException(status_code=400, detail="treasury account must be treasury:mrwk")
    if lower.startswith("reserve:"):
        reserve_prefix = "reserve:bounty:"
        if not lower.startswith(reserve_prefix):
            raise HTTPException(
                status_code=400, detail="reserve account must use reserve:bounty:<id>"
            )
        bounty_id = lower.removeprefix(reserve_prefix)
        try:
            normalized_bounty_id = int(bounty_id) if bounty_id.isdigit() else 0
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="reserve bounty id is too large") from exc
        if normalized_bounty_id <= 0:
            raise HTTPException(status_code=400, detail="reserve bounty id must be positive")
        if normalized_bounty_id > SQLITE_INTEGER_MAX:
            raise HTTPException(status_code=400, detail="reserve bounty id is too large")
        return f"{reserve_prefix}{normalized_bounty_id}"
    if lower.startswith("mrwk1"):
        return clean.lower()
    if lower.startswith("github:"):
        login = clean.split(":", 1)[1].lower()
        if not GITHUB_LOGIN_RE.fullmatch(login):
            raise HTTPException(status_code=400, detail="github login must be valid")
        return f"github:{login}"
    return clean


def _github_login_from_account(account: str) -> str | None:
    if not account.startswith("github:"):
        return None
    login = account.removeprefix("github:")
    if not GITHUB_LOGIN_RE.fullmatch(login):
        return None
    return login


def _positive_bounty_id(bounty_id: int) -> int:
    if bounty_id <= 0:
        raise HTTPException(status_code=400, detail="bounty id must be positive")
    if bounty_id > SQLITE_INTEGER_MAX:
        raise HTTPException(status_code=400, detail="bounty id is too large")
    return bounty_id


def _positive_ledger_sequence(sequence: int) -> int:
    if sequence <= 0:
        raise HTTPException(status_code=400, detail="ledger sequence must be positive")
    if sequence > SQLITE_INTEGER_MAX:
        raise HTTPException(status_code=400, detail="ledger sequence is too large")
    return sequence


def _normalized_wallet_address(address: str) -> str:
    try:
        return normalize_wallet_address(address)
    except WalletError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _proof_hash_from_path(proof_hash: str) -> str:
    if proof_hash != proof_hash.strip():
        raise HTTPException(status_code=400, detail="proof hash must be 64 hex characters")
    clean = proof_hash.lower()
    if not HEX_HASH_RE.fullmatch(clean):
        raise HTTPException(status_code=400, detail="proof hash must be 64 hex characters")
    return clean


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
    if field not in data or data[field] is None:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    value = data[field]
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    return value


def _optional_str(data: dict[str, Any], field: str, default: str = "") -> str:
    value = data.get(field, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    return value


def _parse_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        clean = value.strip()
        if clean and clean.lstrip("+-").isdigit():
            try:
                return int(clean)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{field} must be an integer") from exc
    raise HTTPException(status_code=400, detail=f"{field} must be an integer")


def _required_int(data: dict[str, Any], field: str) -> int:
    value = data.get(field)
    if value is None:
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    return _parse_int(value, field)


def _optional_int(data: dict[str, Any], field: str, default: int) -> int:
    value = data.get(field, default)
    if value is None:
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    return _parse_int(value, field)


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

    def attempt_submitter_account(data: dict[str, Any], github_login: str) -> str:
        submitter_account = f"github:{github_login}"
        if data.get("submitter_account") is None:
            return submitter_account
        requested_account = _normalized_account(_required_str(data, "submitter_account"))
        if requested_account != submitter_account:
            raise HTTPException(status_code=403, detail="submitter_account does not match login")
        return submitter_account

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

    def list_bounties_by_status(
        status: str | None = None, query_text: str | None = None
    ) -> list[dict[str, Any]]:
        with session_scope(db_url) as session:
            query = select(Bounty)
            if status is not None:
                normalized_status = status.strip().lower()
                if normalized_status not in {"open", "paid", "closed"}:
                    raise HTTPException(
                        status_code=400, detail="status must be one of: open, paid, closed"
                    )
                query = query.where(Bounty.status == normalized_status)
            if query_text is not None:
                normalized_query = query_text.strip()
                if normalized_query:
                    escaped_query = (
                        normalized_query.lower()
                        .replace("\\", "\\\\")
                        .replace("%", "\\%")
                        .replace("_", "\\_")
                    )
                    like_query = f"%{escaped_query}%"
                    issue_number = _issue_number_search_value(normalized_query)
                    text_filter = or_(
                        func.lower(Bounty.repo).like(like_query, escape="\\"),
                        func.lower(Bounty.title).like(like_query, escape="\\"),
                        func.lower(Bounty.acceptance).like(like_query, escape="\\"),
                    )
                    if issue_number is not None:
                        text_filter = or_(text_filter, Bounty.issue_number == issue_number)
                    query = query.where(text_filter)
            bounties = session.scalars(query.order_by(Bounty.id.desc())).all()
            return [bounty_to_dict(bounty) for bounty in bounties]

    @app.get("/api/v1/bounties")
    def api_bounties(
        status: str | None = Query(None), q: str | None = Query(None)
    ) -> list[dict[str, Any]]:
        return list_bounties_by_status(status, q)

    @app.get("/api/v1/bounties/summary")
    def api_bounties_summary(
        status: str | None = Query(None), q: str | None = Query(None)
    ) -> dict[str, Any]:
        return bounty_list_summary(list_bounties_by_status(status, q))

    @app.get("/api/v1/admin/webhook-events")
    def api_admin_webhook_events(
        status: str | None = Query(None),
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        admin_login: str = Depends(require_admin_token),
    ) -> list[dict[str, Any]]:
        del admin_login
        normalized_status = status.strip().lower() if status is not None else None
        if normalized_status == "":
            normalized_status = None
        with session_scope(db_url) as session:
            query = select(WebhookEvent)
            if normalized_status is not None:
                query = query.where(func.lower(WebhookEvent.processed_status) == normalized_status)
            events = session.scalars(
                query.order_by(
                    WebhookEvent.created_at.desc(), WebhookEvent.delivery_id.desc()
                ).limit(limit)
            ).all()
            return [
                {
                    "delivery_id": event.delivery_id,
                    "event_type": event.event_type,
                    "processed_status": event.processed_status,
                    "payload_hash": event.payload_hash,
                    "created_at": event.created_at.isoformat(),
                }
                for event in events
            ]

    @app.post("/api/v1/bounties")
    async def api_create_bounty(
        request: Request, admin_login: str = Depends(require_admin_token)
    ) -> dict[str, Any]:
        data = await _json_object(request)
        with session_scope(db_url) as session:
            try:
                bounty = create_bounty(
                    session,
                    repo=_required_str(data, "repo"),
                    issue_number=_required_int(data, "issue_number"),
                    issue_url=_required_str(data, "issue_url"),
                    title=_required_str(data, "title"),
                    reward_mrwk=str(data["reward_mrwk"]),
                    max_awards=_optional_int(data, "max_awards", 1),
                    acceptance=_required_str(data, "acceptance"),
                )
            except KeyError as exc:
                raise HTTPException(status_code=400, detail=f"{exc.args[0]} is required") from exc
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            result = bounty_to_dict(bounty)
            result["created_by"] = admin_login
            return result

    @app.get("/api/v1/bounties/{bounty_id}")
    def api_bounty(bounty_id: int) -> dict[str, Any]:
        bounty_id = _positive_bounty_id(bounty_id)
        with session_scope(db_url) as session:
            bounty = session.get(Bounty, bounty_id)
            if bounty is None:
                raise HTTPException(status_code=404, detail="bounty not found")
            result = bounty_to_dict(bounty)
            result["accepted_awards"] = bounty_awards_to_dict(session, bounty.id)
            return result

    @app.get("/api/v1/bounties/{bounty_id}/attempts")
    def api_bounty_attempts(bounty_id: int, include_expired: bool = Query(False)) -> dict[str, Any]:
        bounty_id = _positive_bounty_id(bounty_id)
        now = _utc_now()
        with session_scope(db_url) as session:
            bounty = session.get(Bounty, bounty_id)
            if bounty is None:
                raise HTTPException(status_code=404, detail="bounty not found")
            query = select(BountyAttempt).where(BountyAttempt.bounty_id == bounty_id)
            if not include_expired:
                query = query.where(*_active_attempt_conditions(bounty_id, now))
            attempts = session.scalars(
                query.order_by(BountyAttempt.created_at.desc(), BountyAttempt.id.desc())
            ).all()
            return {
                "bounty_id": bounty_id,
                "warnings": bounty_attempt_warnings(session, bounty, now),
                "attempts": [bounty_attempt_to_dict(attempt, now) for attempt in attempts],
            }

    @app.post("/api/v1/bounties/{bounty_id}/attempts")
    async def api_create_bounty_attempt(
        bounty_id: int,
        request: Request,
        github_login: str = Depends(require_github_login),
    ) -> JSONResponse:
        bounty_id = _positive_bounty_id(bounty_id)
        data = await _json_object(request)
        submitter_account = attempt_submitter_account(data, github_login)
        ttl_seconds = _optional_int(data, "ttl_seconds", DEFAULT_ATTEMPT_TTL_SECONDS)
        if ttl_seconds < MIN_ATTEMPT_TTL_SECONDS:
            raise HTTPException(status_code=400, detail="ttl_seconds must be at least 60")
        if ttl_seconds > MAX_ATTEMPT_TTL_SECONDS:
            raise HTTPException(status_code=400, detail="ttl_seconds must be no more than 604800")
        source = _optional_str(data, "source_url").strip()
        try:
            source_url = validate_public_url(source) if source else None
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        now = _utc_now()
        with session_scope(db_url) as session:
            bounty = session.get(Bounty, bounty_id)
            if bounty is None:
                raise HTTPException(status_code=404, detail="bounty not found")
            expire_stale_bounty_attempts(session, bounty_id, now, submitter_account)
            awards_remaining = max(0, bounty.max_awards - bounty.awards_paid)
            if bounty.status != "open" or awards_remaining <= 0:
                return JSONResponse(
                    status_code=409,
                    content={
                        "status": "not_available",
                        "bounty_id": bounty_id,
                        "warnings": bounty_attempt_warnings(session, bounty, now),
                    },
                )
            existing = session.scalar(
                select(BountyAttempt)
                .where(
                    *_active_attempt_conditions(bounty_id, now),
                    BountyAttempt.submitter_account == submitter_account,
                )
                .order_by(BountyAttempt.created_at.desc(), BountyAttempt.id.desc())
                .limit(1)
            )
            if existing is not None:
                return JSONResponse(
                    status_code=409,
                    content={
                        "status": "duplicate_active_attempt",
                        "attempt": bounty_attempt_to_dict(existing, now),
                        "warnings": bounty_attempt_warnings(session, bounty, now),
                    },
                )
            attempt = BountyAttempt(
                bounty_id=bounty_id,
                submitter_account=submitter_account,
                source_url=source_url,
                status="active",
                expires_at=now + timedelta(seconds=ttl_seconds),
                created_at=now,
                updated_at=now,
            )
            session.add(attempt)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                bounty = session.get(Bounty, bounty_id)
                existing = session.scalar(
                    select(BountyAttempt)
                    .where(
                        *_active_attempt_conditions(bounty_id, now),
                        BountyAttempt.submitter_account == submitter_account,
                    )
                    .order_by(BountyAttempt.created_at.desc(), BountyAttempt.id.desc())
                    .limit(1)
                )
                if bounty is None or existing is None:
                    raise HTTPException(
                        status_code=409, detail="active attempt already exists"
                    ) from None
                return JSONResponse(
                    status_code=409,
                    content={
                        "status": "duplicate_active_attempt",
                        "attempt": bounty_attempt_to_dict(existing, now),
                        "warnings": bounty_attempt_warnings(session, bounty, now),
                    },
                )
            return JSONResponse(
                status_code=201,
                content={
                    "status": "registered",
                    "attempt": bounty_attempt_to_dict(attempt, now),
                    "warnings": bounty_attempt_warnings(session, bounty, now),
                },
            )

    @app.post("/api/v1/bounty-attempts/{attempt_id}/release")
    async def api_release_bounty_attempt(
        attempt_id: int,
        request: Request,
        github_login: str = Depends(require_github_login),
    ) -> dict[str, Any]:
        if attempt_id <= 0:
            raise HTTPException(status_code=400, detail="attempt id must be positive")
        if attempt_id > SQLITE_INTEGER_MAX:
            raise HTTPException(status_code=400, detail="attempt id is too large")
        data = await _json_object(request)
        submitter_account = attempt_submitter_account(data, github_login)
        now = _utc_now()
        with session_scope(db_url) as session:
            attempt = session.get(BountyAttempt, attempt_id)
            if attempt is None:
                raise HTTPException(status_code=404, detail="attempt not found")
            if attempt.submitter_account != submitter_account:
                raise HTTPException(status_code=403, detail="submitter_account does not match")
            effective_status = _attempt_effective_status(attempt, now)
            if effective_status != "active":
                return {
                    "status": f"already_{effective_status}",
                    "attempt": bounty_attempt_to_dict(attempt, now),
                }
            attempt.status = "released"
            attempt.updated_at = now
            session.flush()
            return {
                "status": "released",
                "attempt": bounty_attempt_to_dict(attempt, now),
            }

    @app.get("/api/v1/reconciliation/payouts")
    def api_payout_reconciliation(
        admin_login: str = Depends(require_admin_token),
    ) -> dict[str, Any]:
        with session_scope(db_url) as session:
            checks = reconcile_accepted_payouts(session)
            return {
                "generated_by": admin_login,
                "summary": payout_reconciliation_summary(checks),
                "checks": [payout_reconciliation_to_dict(check) for check in checks],
            }

    @app.post("/api/v1/bounties/{bounty_id}/pay")
    async def api_pay_bounty(
        bounty_id: int,
        request: Request,
        admin_login: str = Depends(require_admin_token),
    ) -> Any:
        bounty_id = _positive_bounty_id(bounty_id)
        data = await _json_object(request)
        try:
            requested_account = _required_str(data, "to_account")
            submission_url = _required_str(data, "submission_url")
            clean_submission_url = validate_public_url(submission_url)
        except HTTPException as exc:
            if str(exc.detail).endswith(" is required"):
                field = str(exc.detail).removesuffix(" is required")
                raise HTTPException(
                    status_code=400, detail=f"missing required field: {field}"
                ) from exc
            raise
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        accepted_by = _optional_str(data, "accepted_by", admin_login) or admin_login
        verifier_result = {
            "source": "admin_api",
            "accepted_by": accepted_by,
        }
        if data.get("note") is not None:
            note = _optional_str(data, "note").strip()
            if note:
                verifier_result["note"] = note[:240]
        with session_scope(db_url) as session:
            try:
                to_account = resolve_payout_account(session, requested_account)
                proof = pay_bounty(
                    session,
                    bounty_id=bounty_id,
                    to_account=to_account,
                    submission_url=clean_submission_url,
                    accepted_by=accepted_by,
                    verifier_result=verifier_result,
                )
                bounty = session.get(Bounty, bounty_id)
                if bounty is None:
                    raise LedgerError("bounty not found")
                bounty_state = bounty_to_dict(bounty)
                proof_payload = json.loads(proof.public_json)
            except LedgerError as exc:
                if str(exc) == "submission already paid":
                    existing_proof = _existing_payout_proof_for_submission(
                        session, bounty_id, clean_submission_url
                    )
                    if existing_proof is not None:
                        return JSONResponse(
                            status_code=409,
                            content=_payout_response_from_proof(
                                existing_proof, status="already_paid"
                            ),
                        )
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            payout_response = _payout_response_from_proof(proof, status="paid")
            payout_response.update(
                {
                    "bounty_status": bounty_state["status"],
                    "awards_paid": bounty_state["awards_paid"],
                    "awards_remaining": bounty_state["awards_remaining"],
                    "submission_url": proof_payload["submission_url"],
                }
            )
            return payout_response

    @app.post("/api/v1/bounties/{bounty_id}/close")
    async def api_close_bounty(
        bounty_id: int,
        request: Request,
        admin_login: str = Depends(require_admin_token),
    ) -> dict[str, Any]:
        bounty_id = _positive_bounty_id(bounty_id)
        data = await _json_object(request)
        reference = _optional_str(data, "reference") if data.get("reference") is not None else None
        closed_by = _optional_str(data, "closed_by", admin_login)
        with session_scope(db_url) as session:
            try:
                release = close_bounty(
                    session,
                    bounty_id=bounty_id,
                    closed_by=closed_by,
                    reference=reference,
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {
                "status": "closed",
                "bounty_id": bounty_id,
                "released_mrwk": format_mrwk(release.amount_microunits) if release else "0",
                "ledger_sequence": release.sequence if release else None,
            }

    @app.get("/api/v1/accounts/{account}")
    def api_account(account: str) -> dict[str, Any]:
        account = _normalized_account(account)
        github_login = _github_login_from_account(account)
        if account.startswith("github:"):
            transfer_status = (
                "Claim GitHub balances from /me after linking a registered mrwk1 wallet."
            )
        elif account.startswith(("treasury:", "reserve:")):
            transfer_status = (
                "Internal ledger account. MRWK wallet transfers are only available "
                "for registered mrwk1 addresses."
            )
        else:
            transfer_status = "MRWK wallet transfers are enabled for registered mrwk1 addresses."
        with session_scope(db_url) as session:
            account_row = session.get(Account, account)
            accepted_work = safe_account_accepted_summary(session, account)
            return {
                "account": account,
                "ledger_address": account,
                "github_login": github_login,
                "exists": account_row is not None,
                "balance_mrwk": format_mrwk(get_balance(session, account)),
                "transfer_status": transfer_status,
                "accepted_work": accepted_work,
            }

    @app.get("/api/v1/accounts/{account}/accepted-work")
    def api_account_accepted_work(account: str) -> dict[str, Any]:
        account = _normalized_account(account)
        with session_scope(db_url) as session:
            return {
                "account": account,
                "summary": account_accepted_summary(session, account),
                "accepted_work": accepted_work_for_account(session, account),
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

    @app.get("/api/v1/wallets/register", include_in_schema=False)
    def api_register_wallet_get() -> None:
        post_only_route()

    @app.get("/api/v1/wallets/link-github", include_in_schema=False)
    def api_link_wallet_github_get() -> None:
        post_only_route()

    @app.get("/api/v1/wallets/{address}")
    def api_wallet(address: str) -> dict[str, Any]:
        address = _normalized_wallet_address(address)
        with session_scope(db_url) as session:
            wallet = session.get(Wallet, address)
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
    def api_ledger(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict[str, Any]]:
        with session_scope(db_url) as session:
            entries = session.scalars(
                select(LedgerEntry).order_by(LedgerEntry.sequence.desc()).limit(limit)
            ).all()
            proofs = _proof_hashes_by_sequence(session, [entry.sequence for entry in entries])
            return [ledger_to_dict(entry, proofs.get(entry.sequence)) for entry in entries]

    @app.get("/api/v1/ledger/{sequence}")
    def api_ledger_entry(sequence: int) -> dict[str, Any]:
        sequence = _positive_ledger_sequence(sequence)
        with session_scope(db_url) as session:
            entry = session.get(LedgerEntry, sequence)
            if entry is None:
                raise HTTPException(status_code=404, detail="ledger entry not found")
            proof = session.scalar(select(Proof).where(Proof.ledger_sequence == sequence).limit(1))
            return ledger_to_dict(entry, proof.hash if proof else None)

    @app.get("/api/v1/proofs/{proof_hash}")
    def api_proof(proof_hash: str) -> dict[str, Any]:
        proof_hash = _proof_hash_from_path(proof_hash)
        with session_scope(db_url) as session:
            proof = session.get(Proof, proof_hash)
            if proof is None:
                raise HTTPException(status_code=404, detail="proof not found")
            data = proof_public_payload(proof)
            if data is None:
                raise HTTPException(status_code=500, detail="invalid proof payload")
            return data

    @app.get("/api/v1/activity")
    def api_activity(q: str | None = Query(None)) -> dict[str, Any]:
        with session_scope(db_url) as session:
            return activity_to_dict(session, q)

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
    async def mcp(request: Request) -> Any:
        return await handle_mcp_request(request, db_url, _call_mcp_tool)

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
    def bounties_page(
        request: Request, status: str | None = Query(None), q: str | None = Query(None)
    ) -> HTMLResponse:
        selected_status = status.strip().lower() if status is not None else None
        query_text = q.strip() if q is not None else ""
        bounties = list_bounties_by_status(status, q)
        return templates.TemplateResponse(
            request,
            "bounties.html",
            {
                "bounties": bounties,
                "summary": bounty_list_summary(bounties),
                "selected_status": selected_status,
                "query_text": query_text,
            },
        )

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

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request, q: str | None = Query(None)) -> HTMLResponse:
        return templates.TemplateResponse(request, "activity.html", api_activity(q))

    @app.get("/accounts/{account}", response_class=HTMLResponse)
    def account_page(request: Request, account: str) -> HTMLResponse:
        account = _normalized_account(account)
        with session_scope(db_url) as session:
            account_data = api_account(account)
            accepted_summary = safe_account_accepted_summary(session, account)
            entries = session.scalars(
                select(LedgerEntry)
                .where(or_(LedgerEntry.from_account == account, LedgerEntry.to_account == account))
                .order_by(LedgerEntry.sequence.desc())
                .limit(100)
            ).all()
            proofs = _proof_hashes_by_sequence(session, [entry.sequence for entry in entries])
            transactions = [ledger_to_dict(entry, proofs.get(entry.sequence)) for entry in entries]
            accepted_work = safe_accepted_work_for_account(session, account)
        return templates.TemplateResponse(
            request,
            "account.html",
            {
                "account": account_data,
                "accepted_summary": accepted_summary,
                "accepted_work": accepted_work,
                "transactions": transactions,
            },
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
        address = _normalized_wallet_address(address)
        with session_scope(db_url) as session:
            wallet = session.get(Wallet, address)
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
        github_balance_mrwk = "0"
        linked_wallet_address = ""
        if login:
            with session_scope(db_url) as session:
                github_balance_mrwk = format_mrwk(get_balance(session, f"github:{login}"))
                linked_wallet = linked_wallet_for_github(session, login)
                if linked_wallet:
                    linked_wallet_address = linked_wallet.address
        return templates.TemplateResponse(
            request,
            "me.html",
            {
                "github_login": login,
                "github_balance_mrwk": github_balance_mrwk,
                "linked_wallet_address": linked_wallet_address,
            },
        )

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
        webhook_limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> Any:
        login = admin_login_from_request(request)
        if login is None:
            if _oauth_configured(settings):
                return RedirectResponse("/auth/github/login?next=/admin", status_code=302)
            raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
        normalized_status = webhook_status.strip().lower() if webhook_status is not None else ""
        with session_scope(db_url) as session:
            query = select(WebhookEvent)
            if normalized_status:
                query = query.where(func.lower(WebhookEvent.processed_status) == normalized_status)
            webhook_events = session.scalars(
                query.order_by(
                    WebhookEvent.created_at.desc(), WebhookEvent.delivery_id.desc()
                ).limit(webhook_limit)
            ).all()
        return templates.TemplateResponse(
            request,
            "admin.html",
            {
                "login": login,
                "csrf_token": _csrf_token("admin-bounty", login, settings.cookie_secret),
                "webhook_events": webhook_events,
                "webhook_limit": webhook_limit,
                "webhook_limit_options": [10, 25, 50, 100],
                "webhook_status": normalized_status,
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
        max_awards: int = Form(1),
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
                    max_awards=max_awards,
                    acceptance=acceptance,
                )
            except LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            bounty_id = bounty.id
        return RedirectResponse(f"/bounties/{bounty_id}", status_code=303)

    return app


def _call_mcp_tool(database_url: str, name: str, args: dict[str, Any]) -> str | dict[str, Any]:
    def int_arg(field: str) -> int:
        value = args[field]
        if isinstance(value, bool):
            raise ValueError(f"{field} must be an integer")
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str):
            clean = value.strip()
            if clean and clean.lstrip("+-").isdigit():
                try:
                    parsed = int(clean)
                except ValueError as exc:
                    raise ValueError(f"{field} must be an integer") from exc
            else:
                raise ValueError(f"{field} must be an integer")
        else:
            raise ValueError(f"{field} must be an integer")
        if parsed < -SQLITE_INTEGER_MAX - 1 or parsed > SQLITE_INTEGER_MAX:
            raise ValueError(f"{field} is too large")
        return parsed

    def positive_int_arg(field: str) -> int:
        value = int_arg(field)
        if value <= 0:
            raise ValueError(f"{field} must be positive")
        return value

    def str_arg(field: str, *, allow_empty: bool = False) -> str:
        value = args[field]
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        if not allow_empty and value == "":
            raise ValueError(f"{field} must not be empty")
        return value

    def optional_str_arg(field: str, default: str = "") -> str:
        value = args.get(field, default)
        if value is None:
            return default
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        return value

    def optional_clean_str_arg(field: str) -> str | None:
        value = args.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        clean = value.strip()
        return clean or None

    def output_format_arg() -> str:
        value = args.get("format", "text")
        if value is None:
            return "text"
        if not isinstance(value, str):
            raise ValueError("format must be a string")
        normalized = value.strip().lower()
        if normalized not in {"text", "json"}:
            raise ValueError("format must be text or json")
        return normalized

    def mcp_issue_number_search_value(query_text: str) -> int | None:
        if not query_text.isdigit():
            return None
        try:
            issue_number = int(query_text)
        except ValueError:
            return None
        return issue_number if issue_number <= SQLITE_INTEGER_MAX else None

    def list_limit_arg(default: int = 25) -> int:
        if "limit" not in args or args.get("limit") is None:
            return default
        value = positive_int_arg("limit")
        if value > 100:
            raise ValueError("limit must be at most 100")
        return value

    def work_proof_guidance(bounty: Bounty) -> str:
        bounty_data = bounty_to_dict(bounty)
        availability = (
            "open for submissions"
            if bounty_data["status"] == "open" and bounty_data["awards_remaining"] > 0
            else "not currently open for new submissions"
        )
        return "\n".join(
            [
                f"Bounty #{bounty_data['issue_number']}: {bounty_data['title']}",
                f"Internal bounty id: {bounty_data['id']}",
                f"Repository: {bounty_data['repo']}",
                f"Issue: {bounty_data['issue_url']}",
                (
                    f"Status: {bounty_data['status']} ({availability}); "
                    f"awards remaining: {bounty_data['awards_remaining']} "
                    f"of {bounty_data['max_awards']}"
                ),
                f"Reward: {bounty_data['reward_mrwk']} MRWK per accepted award",
                f"Acceptance: {bounty_data['acceptance']}",
                (
                    "Submit: open a focused PR or issue that links this bounty, include "
                    "specific test or behavior evidence, then comment /claim with the PR "
                    "or evidence URL and verification summary."
                ),
                (
                    "Do not include private keys, seed material, secrets, deployment "
                    "credentials, private vulnerability details, or price claims."
                ),
            ]
        )

    def work_proof_guidance_json(bounty: Bounty) -> dict[str, Any]:
        bounty_data = bounty_to_dict(bounty)
        return {
            "bounty_id": bounty_data["id"],
            "issue_number": bounty_data["issue_number"],
            "status": bounty_data["status"],
            "awards_remaining": bounty_data["awards_remaining"],
            "max_awards": bounty_data["max_awards"],
            "awards_paid": bounty_data["awards_paid"],
            "reward_mrwk": bounty_data["reward_mrwk"],
            "available_mrwk": bounty_data["available_mrwk"],
            "repository": bounty_data["repo"],
            "issue_url": bounty_data["issue_url"],
            "title": bounty_data["title"],
            "acceptance": bounty_data["acceptance"],
            "submission_format": (
                "Open a focused PR or issue that links this bounty, include specific "
                "test or behavior evidence, then comment /claim with the PR or "
                "evidence URL and verification summary."
            ),
            "safety_rules": [
                "Do not include private keys, seed material, secrets, deployment "
                "credentials, private vulnerability details, or price claims."
            ],
        }

    def generic_work_proof_guidance_json() -> dict[str, Any]:
        return {
            "bounty_id": None,
            "issue_number": None,
            "status": "generic_guidance",
            "awards_remaining": None,
            "reward_mrwk": None,
            "repository": None,
            "issue_url": None,
            "acceptance": None,
            "submission_format": (
                "Open a focused PR or issue, reference the MRWK bounty, include test "
                "evidence, and wait for a maintainer to apply mrwk:accepted."
            ),
            "safety_rules": [
                "Do not include private keys, seed material, secrets, deployment "
                "credentials, private vulnerability details, or price claims."
            ],
        }

    def optional_bool_arg(field: str, default: bool = False) -> bool:
        value = args.get(field, default)
        if value is None:
            return default
        if not isinstance(value, bool):
            raise ValueError(f"{field} must be a boolean")
        return value

    with session_scope(database_url) as session:
        if name == "list_bounties":
            status = optional_clean_str_arg("status") or "open"
            normalized_status = status.lower()
            if normalized_status not in {"open", "paid", "closed"}:
                raise ValueError("status must be one of: open, paid, closed")
            query = select(Bounty).where(Bounty.status == normalized_status)
            query_text = optional_clean_str_arg("q")
            if query_text:
                escaped_query = (
                    query_text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                like_query = f"%{escaped_query}%"
                issue_number = mcp_issue_number_search_value(query_text)
                text_filter = or_(
                    func.lower(Bounty.repo).like(like_query, escape="\\"),
                    func.lower(Bounty.title).like(like_query, escape="\\"),
                    func.lower(Bounty.acceptance).like(like_query, escape="\\"),
                )
                if issue_number is not None:
                    text_filter = or_(text_filter, Bounty.issue_number == issue_number)
                query = query.where(text_filter)
            bounties = session.scalars(
                query.order_by(Bounty.id.desc()).limit(list_limit_arg())
            ).all()
            return json.dumps([bounty_to_dict(bounty) for bounty in bounties])
        if name == "get_bounty":
            bounty = session.get(Bounty, positive_int_arg("id"))
            if bounty is None:
                return "bounty not found"
            bounty_data = bounty_to_dict(bounty)
            if optional_bool_arg("include_awards"):
                bounty_data["awards"] = bounty_awards_to_dict(session, bounty.id)
            return json.dumps(bounty_data)
        if name == "get_balance":
            account = _normalized_account(str_arg("account"))
            return f"{account}: {format_mrwk(get_balance(session, account))} MRWK"
        if name == "register_wallet":
            wallet = register_wallet(
                session,
                public_key_hex=str_arg("public_key_hex"),
                label=optional_str_arg("label") if args.get("label") is not None else None,
            )
            return json.dumps(wallet_to_dict(session, wallet))
        if name == "get_wallet":
            wallet_row = session.get(Wallet, _normalized_wallet_address(str_arg("address")))
            if wallet_row is None:
                return "wallet not found"
            return json.dumps(wallet_to_dict(session, wallet_row))
        if name == "submit_wallet_transfer":
            transfer = submit_wallet_transfer(
                session,
                from_address=str_arg("from_address"),
                to_address=str_arg("to_address"),
                amount_mrwk=str_arg("amount_mrwk"),
                nonce=int_arg("nonce"),
                memo=optional_str_arg("memo"),
                signature_hex=str_arg("signature_hex"),
            )
            return json.dumps(wallet_transfer_to_dict(transfer))
        if name == "get_ledger_entry":
            entry = session.get(LedgerEntry, positive_int_arg("sequence"))
            if entry is None:
                return "ledger entry not found"
            proof = session.scalar(
                select(Proof).where(Proof.ledger_sequence == entry.sequence).limit(1)
            )
            return json.dumps(ledger_to_dict(entry, proof.hash if proof else None))
        if name == "get_proof":
            proof = session.get(Proof, _proof_hash_from_path(str_arg("hash")))
            if proof is None:
                return "proof not found"
            public_payload = proof_public_payload(proof)
            if public_payload is None:
                raise ValueError("invalid proof payload")
            return json.dumps(
                {
                    "hash": proof.hash,
                    "kind": proof.kind,
                    "ledger_sequence": proof.ledger_sequence,
                    "bounty_id": proof.bounty_id,
                    "submission_id": proof.submission_id,
                    "created_at": proof.created_at.isoformat(),
                    "proof": public_payload,
                }
            )
        if name == "submit_work_proof":
            output_format = output_format_arg()
            has_bounty_id = "bounty_id" in args and args.get("bounty_id") is not None
            has_issue_number = "issue_number" in args and args.get("issue_number") is not None
            if has_bounty_id and has_issue_number:
                raise ValueError("use bounty_id or issue_number, not both")
            if has_bounty_id:
                bounty = session.get(Bounty, positive_int_arg("bounty_id"))
                if bounty is None:
                    return "bounty not found"
                return (
                    work_proof_guidance_json(bounty)
                    if output_format == "json"
                    else work_proof_guidance(bounty)
                )
            if has_issue_number:
                bounties = session.scalars(
                    select(Bounty)
                    .where(Bounty.issue_number == positive_int_arg("issue_number"))
                    .order_by(Bounty.id.desc())
                    .limit(2)
                ).all()
                if not bounties:
                    return "bounty not found"
                if len(bounties) > 1:
                    raise ValueError("issue_number matches multiple bounties")
                return (
                    work_proof_guidance_json(bounties[0])
                    if output_format == "json"
                    else work_proof_guidance(bounties[0])
                )
            if output_format == "json":
                return generic_work_proof_guidance_json()
            return (
                "Open a focused PR or issue, reference the MRWK bounty, include test evidence, "
                "and wait for a maintainer to apply mrwk:accepted."
            )
    raise ValueError("unknown tool")


app = create_app()
