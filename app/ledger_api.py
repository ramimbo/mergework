from __future__ import annotations

import json
import re
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import LedgerEntry, Proof
from app.serializers import ledger_to_dict

SQLITE_INTEGER_MAX = 2**63 - 1
PROOF_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def register_ledger_api_routes(app: FastAPI, *, database_url: str) -> None:
    @app.get("/api/v1/ledger")
    def api_ledger(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict[str, Any]]:
        return ledger_entries(database_url, limit)

    @app.get("/api/v1/ledger/{sequence}")
    def api_ledger_entry(sequence: int) -> dict[str, Any]:
        return ledger_entry(database_url, sequence)

    @app.get("/api/v1/proofs/{proof_hash}")
    def api_proof(proof_hash: str) -> dict[str, Any]:
        return proof_payload(database_url, proof_hash)


def ledger_entries(database_url: str, limit: int = 50) -> list[dict[str, Any]]:
    with session_scope(database_url) as session:
        entries = session.scalars(
            select(LedgerEntry).order_by(LedgerEntry.sequence.desc()).limit(limit)
        ).all()
        proofs = proof_hashes_by_sequence(session, [entry.sequence for entry in entries])
        return [ledger_to_dict(entry, proofs.get(entry.sequence)) for entry in entries]


def ledger_entry(database_url: str, sequence: int) -> dict[str, Any]:
    sequence = positive_ledger_sequence(sequence)
    with session_scope(database_url) as session:
        entry = session.get(LedgerEntry, sequence)
        if entry is None:
            raise HTTPException(status_code=404, detail="ledger entry not found")
        proof = session.scalar(select(Proof).where(Proof.ledger_sequence == sequence).limit(1))
        return ledger_to_dict(entry, proof.hash if proof else None)


def proof_payload(database_url: str, proof_hash: str) -> dict[str, Any]:
    proof_hash = proof_hash_from_path(proof_hash)
    with session_scope(database_url) as session:
        proof = session.get(Proof, proof_hash)
        if proof is None:
            raise HTTPException(status_code=404, detail="proof not found")
        data = json.loads(proof.public_json)
        if not isinstance(data, dict):
            raise HTTPException(status_code=500, detail="invalid proof payload")
        return data


def proof_hashes_by_sequence(session: Session, sequences: list[int]) -> dict[int, str]:
    if not sequences:
        return {}
    rows = session.execute(
        select(Proof.ledger_sequence, Proof.hash).where(Proof.ledger_sequence.in_(sequences))
    ).all()
    return {int(sequence): str(proof_hash) for sequence, proof_hash in rows}


def positive_ledger_sequence(sequence: int) -> int:
    if sequence <= 0:
        raise HTTPException(status_code=400, detail="ledger sequence must be positive")
    if sequence > SQLITE_INTEGER_MAX:
        raise HTTPException(status_code=400, detail="ledger sequence is too large")
    return sequence


def proof_hash_from_path(proof_hash: str) -> str:
    if proof_hash != proof_hash.strip():
        raise HTTPException(status_code=400, detail="proof hash must be 64 hex characters")
    clean = proof_hash.lower()
    if not PROOF_HASH_RE.fullmatch(clean):
        raise HTTPException(status_code=400, detail="proof hash must be 64 hex characters")
    return clean
