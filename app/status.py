from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ledger.service import (
    GENESIS_SUPPLY_MICRO,
    TREASURY_ACCOUNT,
    format_mrwk,
    get_balance,
)
from app.models import Bounty, LedgerEntry

CURRENT_TRANSFER_PATHS = (
    "github:* balance claims into a linked wallet",
    "payouts to linked mrwk1 wallets",
    "signed wallet-to-wallet transfers between registered wallets",
)
UNSUPPORTED_PUBLIC_PATHS = ("BTC", "USDC", "fiat", "bridge", "exchange", "off-ramp")
UNSUPPORTED_PUBLIC_PATHS_SUMMARY = (
    "MergeWork does not currently operate a public BTC, USDC, fiat, bridge, exchange, or off-ramp."
)
FUTURE_PATH_BOUNDARY = (
    "Future public snapshots, bridges, and onchain claims require separate "
    "maintainer/contributor discussion before implementation."
)


def ledger_height(session: Session) -> int:
    height = session.scalar(select(func.max(LedgerEntry.sequence))) or 0
    return int(height)


def health_status(session: Session) -> dict[str, Any]:
    return {
        "ok": True,
        "service": "mergework",
        "ticker": "MRWK",
        "ledger_height": ledger_height(session),
    }


def system_status(session: Session) -> dict[str, Any]:
    active_bounties = session.scalar(
        select(func.count()).select_from(Bounty).where(Bounty.status == "open")
    )
    treasury_balance = get_balance(session, TREASURY_ACCOUNT)
    return {
        "name": "MergeWork",
        "ticker": "MRWK",
        "genesis_supply_mrwk": format_mrwk(GENESIS_SUPPLY_MICRO),
        "ledger_height": ledger_height(session),
        "active_bounties": int(active_bounties or 0),
        "treasury_balance_mrwk": format_mrwk(treasury_balance),
        "current_transfer_paths": list(CURRENT_TRANSFER_PATHS),
        "unsupported_public_paths": list(UNSUPPORTED_PUBLIC_PATHS),
        "unsupported_public_paths_summary": UNSUPPORTED_PUBLIC_PATHS_SUMMARY,
        "future_path": "public snapshots, bridges, and onchain claims",
        "future_path_boundary": FUTURE_PATH_BOUNDARY,
    }
