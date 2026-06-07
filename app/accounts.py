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
from app.query_validation import reject_repeated_query_param, reject_unsupported_query_params
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
        raise HTTPException(status_code=400, detail="Invalid account") from None
    if contains_control_character(account):
        raise HTTPException(status_code=400, detail="Account contains control characters") from None
    if not GITHUB_LOGIN_RE.fullmatch(account):
        raise HTTPException(status_code=400, detail="Invalid GitHub login") from None
    return account


@router.get("/api/v1/accounts/{account}/accepted-work")
async def api_account_accepted_work(
    request: Request,
    account: str = Depends(normalized_account),
    session: Session = Depends(get_session),
):
    reject_unsupported_query_params(request, ("type", "tx_type"), context="account accepted-work")
    # existing code...