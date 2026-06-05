from __future__ import annotations

import re

from fastapi import HTTPException

from app.control_chars import contains_control_character

SQLITE_INTEGER_MAX = 2**63 - 1
HEX_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
POSITIVE_INTEGER_RE = re.compile(r"^[0-9]+$")


def reject_path_whitespace_padding(value: str, field: str) -> None:
    """Reject leading or trailing path whitespace before identifier normalization."""
    if contains_control_character(value):
        return
    if value.strip() and value != value.strip():
        raise HTTPException(
            status_code=400,
            detail=f"{field} must not contain leading or trailing whitespace",
        )


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


def positive_path_int(value: int | str, field: str) -> int:
    if isinstance(value, str):
        if not POSITIVE_INTEGER_RE.fullmatch(value):
            raise HTTPException(status_code=400, detail=f"{field} must be a positive integer")
        if len(value) > 1 and value.startswith("0"):
            raise HTTPException(
                status_code=400, detail=f"{field} must be a canonical positive integer"
            )
        try:
            parsed = int(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{field} is too large") from exc
    else:
        parsed = value
    if parsed <= 0:
        raise HTTPException(status_code=400, detail=f"{field} must be positive")
    if parsed > SQLITE_INTEGER_MAX:
        raise HTTPException(status_code=400, detail=f"{field} is too large")
    return parsed


def positive_bounty_id(bounty_id: int | str) -> int:
    return positive_path_int(bounty_id, "bounty id")


def positive_ledger_sequence(sequence: int | str) -> int:
    return positive_path_int(sequence, "ledger sequence")


def positive_proposal_id(proposal_id: int | str) -> int:
    return positive_path_int(proposal_id, "proposal id")


def proof_hash_from_path(proof_hash: str) -> str:
    if proof_hash != proof_hash.strip():
        raise HTTPException(status_code=400, detail="proof hash must be 64 hex characters")
    clean = proof_hash.lower()
    if not HEX_HASH_RE.fullmatch(clean):
        raise HTTPException(status_code=400, detail="proof hash must be 64 hex characters")
    return clean
