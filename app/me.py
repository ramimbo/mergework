from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ledger.service import format_mrwk, get_balance, linked_wallet_for_github


def me_page_context(session: Session, login: str | None) -> dict[str, Any]:
    github_balance_mrwk = "0"
    linked_wallet_address = ""
    github_account_url = ""
    linked_wallet_url = ""
    if login:
        github_balance_mrwk = format_mrwk(get_balance(session, f"github:{login}"))
        github_account_url = f"/accounts/github:{login}"
        linked_wallet = linked_wallet_for_github(session, login)
        if linked_wallet:
            linked_wallet_address = linked_wallet.address
            linked_wallet_url = f"/wallets/{linked_wallet.address}"
    return {
        "github_login": login,
        "github_balance_mrwk": github_balance_mrwk,
        "linked_wallet_address": linked_wallet_address,
        "github_account_url": github_account_url,
        "linked_wallet_url": linked_wallet_url,
    }
