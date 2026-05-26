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
    "github:* balance claims",
    "linked mrwk1 wallet payouts",
    "signed wallet-to-wallet transfers",
)
UNSUPPORTED_PUBLIC_PATHS = (
    "public BTC/USDC/fiat bridge",
    "exchange",
    "off-ramp",
)
FUTURE_PATH_NOTE = (
    "native ledger claims and transfers today; future public snapshots, bridges, "
    "and onchain claims require separate maintainer/contributor discussion"
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
        "future_path": FUTURE_PATH_NOTE,
    }
