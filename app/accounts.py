from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.control_chars import contains_control_character
from app.db import session_scope
from app.ledger.service import TREASURY_ACCOUNT, format_mrwk, get_balance
from app.ledger_views import account_ledger_transactions
from app.models import Account
from app.path_params import SQLITE_INTEGER_MAX, reject_path_whitespace_padding
from app.query_validation import reject_repeated_query_param
from app.serializers import (
    accepted_work_for_account,
    account_accepted_summary,
    pending_payout_summary,
    pending_payouts_for_account,
    safe_accepted_work_for_account,
    safe_account_accepted_summary,
    safe_pending_payouts_for_account,
)
from app.wallets import WalletError, normalize_wallet_address

GITHUB_LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")
ACCOUNT_TRANSACTION_TYPE_OPTIONS = [
    {"value": "all", "label": "All"},
    {"value": "bounty_payment", "label": "Bounty payments"},
    {"value": "bounty_reserve", "label": "Bounty reserves"},
    {"value": "bounty_release", "label": "Bounty releases"},
    {"value": "github_claim", "label": "GitHub claims"},
    {"value": "wallet_transfer", "label": "Wallet transfers"},
    {"value": "genesis", "label": "Genesis"},
]
ACCOUNT_TRANSACTION_TYPES = {
    str(option["value"]) for option in ACCOUNT_TRANSACTION_TYPE_OPTIONS if option["value"] != "all"
}


def normalized_wallet_address(address: str) -> str:
    try:
        return normalize_wallet_address(address)
    except WalletError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def normalized_account(account: str) -> str:
    if not account or not account.strip():
        raise HTTPException(status_code=400, detail="account must not be empty")
    if contains_control_character(account):
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
        return normalized_wallet_address(clean)
    if lower.startswith("github:"):
        login = clean.split(":", 1)[1].lower()
        if not GITHUB_LOGIN_RE.fullmatch(login):
            raise HTTPException(status_code=400, detail="github login must be valid")
        return f"github:{login}"
    return clean


def github_login_from_account(account: str) -> str | None:
    if not account.startswith("github:"):
        return None
    login = account.removeprefix("github:")
    if not GITHUB_LOGIN_RE.fullmatch(login):
        return None
    return login


def account_transfer_status(account: str) -> str:
    if account.startswith("github:"):
        return "Claim GitHub balances from /me after linking a registered mrwk1 wallet."
    if account.startswith(("treasury:", "reserve:")):
        return (
            "Internal ledger account. MRWK wallet transfers are only available "
            "for registered mrwk1 addresses."
        )
    if account.startswith("mrwk1"):
        return "MRWK wallet transfers are enabled for registered mrwk1 addresses."
    return "MRWK wallet transfers require a registered mrwk1 address."


def account_action_urls(account: str) -> dict[str, str]:
    path_account = quote(account, safe=":")
    return {
        "account_json": f"/api/v1/accounts/{path_account}",
        "accepted_work_json": f"/api/v1/accounts/{path_account}/accepted-work",
        "activity": f"/api/v1/activity?{urlencode({'account': account})}",
    }


def _account_api_context_for_normalized_account(session: Session, account: str) -> dict[str, Any]:
    account_row = session.get(Account, account)
    pending_payouts = safe_pending_payouts_for_account(session, account)
    return {
        "account": account,
        "ledger_address": account,
        "github_login": github_login_from_account(account),
        "exists": account_row is not None,
        "balance_mrwk": format_mrwk(get_balance(session, account)),
        "transfer_status": account_transfer_status(account),
        "accepted_work": safe_account_accepted_summary(session, account),
        "pending_summary": pending_payout_summary(pending_payouts),
        "pending_payouts": pending_payouts,
    }


def account_api_context(session: Session, account: str) -> dict[str, Any]:
    return _account_api_context_for_normalized_account(session, normalized_account(account))


def account_accepted_work_context(session: Session, account: str) -> dict[str, Any]:
    account = normalized_account(account)
    pending_payouts = pending_payouts_for_account(session, account)
    return {
        "account": account,
        "summary": account_accepted_summary(session, account),
        "accepted_work": accepted_work_for_account(session, account),
        "pending_summary": pending_payout_summary(pending_payouts),
        "pending_payouts": pending_payouts,
    }


def _transaction_type_filter(tx_type: str | None) -> tuple[str, str | None]:
    if tx_type is not None and contains_control_character(tx_type):
        raise HTTPException(
            status_code=400, detail="transaction type must not contain control characters"
        )
    selected = (tx_type or "all").strip().lower()
    if selected in {"", "all"}:
        return "all", None
    if selected not in ACCOUNT_TRANSACTION_TYPES:
        allowed = ", ".join(option["value"] for option in ACCOUNT_TRANSACTION_TYPE_OPTIONS)
        raise HTTPException(
            status_code=400,
            detail=f"transaction type must be one of: {allowed}",
        )
    return selected, selected


def account_page_context(
    session: Session, account: str, transaction_type: str | None = None
) -> dict[str, Any]:
    account = normalized_account(account)
    selected_transaction_type, transaction_filter = _transaction_type_filter(transaction_type)
    account_context = _account_api_context_for_normalized_account(session, account)
    return {
        "account": account_context,
        "account_action_urls": account_action_urls(account),
        "accepted_summary": account_context["accepted_work"],
        "accepted_work": safe_accepted_work_for_account(session, account),
        "pending_summary": account_context["pending_summary"],
        "pending_payouts": account_context["pending_payouts"],
        "selected_transaction_type": selected_transaction_type,
        "transaction_type_options": ACCOUNT_TRANSACTION_TYPE_OPTIONS,
        "transactions": account_ledger_transactions(
            session, account, entry_type=transaction_filter
        ),
    }


def register_account_routes(app: FastAPI, *, db_url: str, templates: Jinja2Templates) -> None:
    @app.get("/api/v1/accounts/{account}")
    def api_account(account: str) -> dict[str, Any]:
        reject_path_whitespace_padding(account, "account")
        with session_scope(db_url) as session:
            return account_api_context(session, account)

    @app.get("/api/v1/accounts/{account}/accepted-work")
    def api_account_accepted_work(account: str) -> dict[str, Any]:
        reject_path_whitespace_padding(account, "account")
        with session_scope(db_url) as session:
            return account_accepted_work_context(session, account)

    @app.get("/accounts/{account}", response_class=HTMLResponse)
    def account_page(
        request: Request, account: str, tx_type: str | None = Query(None)
    ) -> HTMLResponse:
        reject_path_whitespace_padding(account, "account")
        for value in request.query_params.getlist("tx_type"):
            if contains_control_character(value):
                raise HTTPException(
                    status_code=400, detail="transaction type must not contain control characters"
                )
        reject_repeated_query_param(request, "tx_type")
        with session_scope(db_url) as session:
            context = account_page_context(session, account, tx_type)
        return templates.TemplateResponse(request, "account.html", context)
