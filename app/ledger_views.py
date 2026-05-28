from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import LedgerEntry, Proof
from app.serializers import ledger_to_dict


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


def recent_ledger_entries(session: Session, limit: int) -> list[dict[str, Any]]:
    entries = session.scalars(
        select(LedgerEntry).order_by(LedgerEntry.sequence.desc()).limit(limit)
    ).all()
    return ledger_entries_to_dicts(session, entries)


def ledger_entry_to_dict(session: Session, sequence: int) -> dict[str, Any] | None:
    entry = session.get(LedgerEntry, sequence)
    if entry is None:
        return None
    return ledger_entries_to_dicts(session, [entry])[0]


def account_ledger_transactions(
    session: Session, account: str, limit: int = 100, entry_type: str | None = None
) -> list[dict[str, Any]]:
    query = select(LedgerEntry).where(
        or_(LedgerEntry.from_account == account, LedgerEntry.to_account == account)
    )
    if entry_type is not None:
        query = query.where(LedgerEntry.entry_type == entry_type)
    entries = session.scalars(query.order_by(LedgerEntry.sequence.desc()).limit(limit)).all()
    return ledger_entries_to_dicts(session, entries)


def account_ledger_transaction_types(session: Session, account: str) -> list[str]:
    return [
        str(entry_type)
        for entry_type in session.scalars(
            select(LedgerEntry.entry_type)
            .where(or_(LedgerEntry.from_account == account, LedgerEntry.to_account == account))
            .distinct()
            .order_by(LedgerEntry.entry_type.asc())
        ).all()
    ]
