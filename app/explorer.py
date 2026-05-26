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


def ledger_transactions_for_account(
    session: Session, account: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    entries = session.scalars(
        select(LedgerEntry)
        .where(or_(LedgerEntry.from_account == account, LedgerEntry.to_account == account))
        .order_by(LedgerEntry.sequence.desc())
        .limit(limit)
    ).all()
    proofs = proof_hashes_by_sequence(session, [entry.sequence for entry in entries])
    return [ledger_to_dict(entry, proofs.get(entry.sequence)) for entry in entries]
