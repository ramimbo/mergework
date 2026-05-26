from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import LedgerEntry, Proof, Wallet
from app.serializers import ledger_to_dict, wallet_to_dict
from app.wallets import WalletError, normalize_wallet_address


def wallet_address_from_path(address: str) -> str:
    try:
        return normalize_wallet_address(address)
    except WalletError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def wallet_list_context(database_url: str, limit: int = 100) -> dict[str, Any]:
    with session_scope(database_url) as session:
        wallets = session.scalars(
            select(Wallet).order_by(Wallet.created_at.desc()).limit(limit)
        ).all()
        return {"wallets": [wallet_to_dict(session, wallet) for wallet in wallets]}


def wallet_detail_context(database_url: str, address: str, limit: int = 100) -> dict[str, Any]:
    address = wallet_address_from_path(address)
    with session_scope(database_url) as session:
        wallet = session.get(Wallet, address)
        if wallet is None:
            raise HTTPException(status_code=404, detail="wallet not found")
        return {
            "wallet": wallet_to_dict(session, wallet),
            "transactions": wallet_transaction_rows(session, wallet.address, limit),
        }


def wallet_transaction_rows(
    session: Session, wallet_address: str, limit: int = 100
) -> list[dict[str, Any]]:
    entries = session.scalars(
        select(LedgerEntry)
        .where(
            or_(
                LedgerEntry.from_account == wallet_address,
                LedgerEntry.to_account == wallet_address,
            )
        )
        .order_by(LedgerEntry.sequence.desc())
        .limit(limit)
    ).all()
    proofs = proof_hashes_by_sequence(session, [entry.sequence for entry in entries])
    return [ledger_to_dict(entry, proofs.get(entry.sequence)) for entry in entries]


def proof_hashes_by_sequence(session: Session, sequences: list[int]) -> dict[int, str]:
    if not sequences:
        return {}
    rows = session.execute(
        select(Proof.ledger_sequence, Proof.hash).where(Proof.ledger_sequence.in_(sequences))
    ).all()
    return {int(sequence): str(proof_hash) for sequence, proof_hash in rows}
