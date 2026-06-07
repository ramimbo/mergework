from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.accounts import normalized_wallet_address
from app.ledger.service import format_mrwk, get_balance, linked_wallet_for_github


def me_page_context(
    session: Session, login: str | None, link_address: str | None = None
) -> dict[str, Any]:
    github_balance_mrwk = "0"
    linked_wallet_address = ""
    link_wallet_address = normalized_wallet_address(link_address) if link_address else ""
    sign_in_next_path = f"/me?address={link_wallet_address}" if link_wallet_address else "/me"
    if login:
        github_balance_mrwk = format_mrwk(get_balance(session, f"github:{login}"))
        linked_wallet = linked_wallet_for_github(session, login)
        if linked_wallet:
            linked_wallet_address = linked_wallet.address
    return {
        "github_login": login,
        "github_balance_mrwk": github_balance_mrwk,
        "linked_wallet_address": linked_wallet_address,
        "link_wallet_address": link_wallet_address,
        "sign_in_url": f"/auth/github/login?next={sign_in_next_path}",
    }
