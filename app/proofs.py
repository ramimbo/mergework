from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Proof

PROOF_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def proof_hash_from_path(proof_hash: str) -> str:
    if proof_hash != proof_hash.strip():
        raise HTTPException(status_code=400, detail="proof hash must be 64 hex characters")
    clean = proof_hash.lower()
    if not PROOF_HASH_RE.fullmatch(clean):
        raise HTTPException(status_code=400, detail="proof hash must be 64 hex characters")
    return clean


def proof_hashes_by_sequence(session: Session, sequences: list[int]) -> dict[int, str]:
    if not sequences:
        return {}
    rows = session.execute(
        select(Proof.ledger_sequence, Proof.hash).where(Proof.ledger_sequence.in_(sequences))
    ).all()
    return {int(sequence): str(proof_hash) for sequence, proof_hash in rows}


def proof_hash_for_sequence(session: Session, sequence: int) -> str | None:
    return session.scalar(select(Proof.hash).where(Proof.ledger_sequence == sequence).limit(1))


def public_proof_payload(proof: Proof) -> dict[str, Any]:
    try:
        payload = json.loads(proof.public_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="invalid proof payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="invalid proof payload")
    return payload


def mcp_proof_to_dict(proof: Proof) -> dict[str, Any]:
    return {
        "hash": proof.hash,
        "kind": proof.kind,
        "ledger_sequence": proof.ledger_sequence,
        "bounty_id": proof.bounty_id,
        "submission_id": proof.submission_id,
        "created_at": proof.created_at.isoformat(),
        "proof": public_proof_payload(proof),
    }
