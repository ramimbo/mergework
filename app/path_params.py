from __future__ import annotations

import re

from fastapi import HTTPException

SQLITE_INTEGER_MAX = 2**63 - 1
HEX_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def issue_number_search_value(query: str) -> int | None:
    """Return a bounded GitHub issue number from a plain numeric search query."""
    clean = query.removeprefix("#")
    if not clean.isdigit():
        return None
    try:
        issue_number = int(clean)
    except ValueError:
        return None
    return issue_number if issue_number <= SQLITE_INTEGER_MAX else None


def positive_bounty_id(bounty_id: int) -> int:
    if bounty_id <= 0:
        raise HTTPException(status_code=400, detail="bounty id must be positive")
    if bounty_id > SQLITE_INTEGER_MAX:
        raise HTTPException(status_code=400, detail="bounty id is too large")
    return bounty_id


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
    if not HEX_HASH_RE.fullmatch(clean):
        raise HTTPException(status_code=400, detail="proof hash must be 64 hex characters")
    return clean
