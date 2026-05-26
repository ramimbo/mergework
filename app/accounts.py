from __future__ import annotations

import re
from typing import Any

from app.ledger.service import TREASURY_ACCOUNT
from app.wallets import WalletError, normalize_wallet_address

GITHUB_LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")
SQLITE_INTEGER_MAX = 2**63 - 1

GITHUB_TRANSFER_STATUS = "Claim GitHub balances from /me after linking a registered mrwk1 wallet."
INTERNAL_LEDGER_TRANSFER_STATUS = (
    "Internal ledger account. MRWK wallet transfers are only available "
    "for registered mrwk1 addresses."
)
WALLET_TRANSFER_STATUS = "MRWK wallet transfers are enabled for registered mrwk1 addresses."


class AccountError(ValueError):
    pass


def normalize_account(account: str) -> str:
    if not account or not account.strip():
        raise AccountError("account must not be empty")
    if re.search(r"[\x00-\x1f\x7f]", account):
        raise AccountError("account must not contain control characters")
    clean = account.strip()
    lower = clean.lower()
    if lower == TREASURY_ACCOUNT:
        return TREASURY_ACCOUNT
    if lower.startswith("treasury:"):
        raise AccountError("treasury account must be treasury:mrwk")
    if lower.startswith("reserve:"):
        return _normalize_reserve_account(lower)
    if lower.startswith("mrwk1"):
        try:
            return normalize_wallet_address(clean)
        except WalletError as exc:
            raise AccountError(str(exc)) from exc
    if lower.startswith("github:"):
        login = clean.split(":", 1)[1].lower()
        if not GITHUB_LOGIN_RE.fullmatch(login):
            raise AccountError("github login must be valid")
        return f"github:{login}"
    return clean


def github_login_from_account(account: str) -> str | None:
    if not account.startswith("github:"):
        return None
    login = account.removeprefix("github:")
    if not GITHUB_LOGIN_RE.fullmatch(login):
        return None
    return login


def transfer_status_for_account(account: str) -> str:
    if account.startswith("github:"):
        return GITHUB_TRANSFER_STATUS
    if account.startswith(("treasury:", "reserve:")):
        return INTERNAL_LEDGER_TRANSFER_STATUS
    return WALLET_TRANSFER_STATUS


def account_summary(
    account: str, *, exists: bool, balance_mrwk: str, accepted_work: dict[str, Any]
) -> dict[str, Any]:
    return {
        "account": account,
        "ledger_address": account,
        "github_login": github_login_from_account(account),
        "exists": exists,
        "balance_mrwk": balance_mrwk,
        "transfer_status": transfer_status_for_account(account),
        "accepted_work": accepted_work,
    }


def _normalize_reserve_account(lower_account: str) -> str:
    reserve_prefix = "reserve:bounty:"
    if not lower_account.startswith(reserve_prefix):
        raise AccountError("reserve account must use reserve:bounty:<id>")
    bounty_id = lower_account.removeprefix(reserve_prefix)
    try:
        normalized_bounty_id = int(bounty_id) if bounty_id.isdigit() else 0
    except ValueError as exc:
        raise AccountError("reserve bounty id is too large") from exc
    if normalized_bounty_id <= 0:
        raise AccountError("reserve bounty id must be positive")
    if normalized_bounty_id > SQLITE_INTEGER_MAX:
        raise AccountError("reserve bounty id is too large")
    return f"{reserve_prefix}{normalized_bounty_id}"
