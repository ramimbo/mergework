from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ledger.service import format_mrwk, get_balance, linked_wallet_for_github


def me_page_context(session: Session, login: str | None) -> dict[str, Any]:
    github_balance_mrwk = "0"
    github_account = ""
    linked_wallet_address = ""
    if login:
        github_account = f"github:{login}"
        github_balance_mrwk = format_mrwk(get_balance(session, github_account))
        linked_wallet = linked_wallet_for_github(session, login)
        if linked_wallet:
            linked_wallet_address = linked_wallet.address
    return {
        "github_login": login,
        "github_account": github_account,
        "github_balance_mrwk": github_balance_mrwk,
        "linked_wallet_address": linked_wallet_address,
    }
