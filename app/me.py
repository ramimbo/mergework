from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ledger.service import format_mrwk, get_balance, linked_wallet_for_github


def _github_account_summary(session: Session, login: str) -> dict[str, str]:
    linked_wallet = linked_wallet_for_github(session, login)
    return {
        "github_balance_mrwk": format_mrwk(get_balance(session, f"github:{login}")),
        "linked_wallet_address": linked_wallet.address if linked_wallet else "",
    }


def me_page_context(session: Session, login: str | None) -> dict[str, Any]:
    context = {
        "github_login": login,
        "github_balance_mrwk": "0",
        "linked_wallet_address": "",
    }
    if login:
        context.update(_github_account_summary(session, login))
    return context
