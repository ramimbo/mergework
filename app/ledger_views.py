from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import LedgerEntry, Proof
from app.serializers import ledger_to_dict

LEDGER_TYPE_LABELS = {
    "bounty_reserve": "Bounty Reserve",
    "bounty_payment": "Bounty Payment",
    "bounty_release": "Bounty Release",
    "github_claim": "GitHub claim",
    "wallet_transfer": "Wallet transfer",
    "genesis": "Genesis",
}
LEDGER_TYPE_FILTER_ERROR = "type must be one of: all, " + ", ".join(LEDGER_TYPE_LABELS.keys())


def normalize_ledger_type_filter(entry_type: str | None) -> str | None:
    if entry_type is None:
        return None
    normalized = entry_type.strip().lower()
    if not normalized or normalized == "all":
        return None
    if normalized not in LEDGER_TYPE_LABELS:
        raise ValueError(LEDGER_TYPE_FILTER_ERROR)
    return normalized


def proof_hashes_by_sequence(session: Session, sequences: Sequence[int]) -> dict[int, str]:
    if not sequences:
        return {}
    rows = session.execute(
        select(Proof.ledger_sequence, Proof.hash).where(Proof.ledger_sequence.in_(sequences))
    ).all()
    return {int(sequence): str(proof_hash) for sequence, proof_hash in rows}


def ledger_entries_to_dicts(
    session: Session, entries: Sequence[LedgerEntry]
) -> list[dict[str, Any]]:
    proofs = proof_hashes_by_sequence(session, [entry.sequence for entry in entries])
    return [ledger_to_dict(entry, proofs.get(entry.sequence)) for entry in entries]


def recent_ledger_entries(
    session: Session, limit: int, entry_type: str | None = None
) -> list[dict[str, Any]]:
    normalized_type = normalize_ledger_type_filter(entry_type)
    query = select(LedgerEntry)
    if normalized_type is not None:
        query = query.where(LedgerEntry.entry_type == normalized_type)
    entries = session.scalars(query.order_by(LedgerEntry.sequence.desc()).limit(limit)).all()
    return ledger_entries_to_dicts(session, entries)


def ledger_entry_to_dict(session: Session, sequence: int) -> dict[str, Any] | None:
    entry = session.get(LedgerEntry, sequence)
    if entry is None:
        return None
    return ledger_entries_to_dicts(session, [entry])[0]


def account_ledger_transactions(
    session: Session, account: str, limit: int = 100
) -> list[dict[str, Any]]:
    entries = session.scalars(
        select(LedgerEntry)
        .where(or_(LedgerEntry.from_account == account, LedgerEntry.to_account == account))
        .order_by(LedgerEntry.sequence.desc())
        .limit(limit)
    ).all()
    return ledger_entries_to_dicts(session, entries)
