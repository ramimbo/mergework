from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException, Request

from app.wallets import WalletError, normalize_wallet_address

SQLITE_INTEGER_MAX = 2**63 - 1
HEX_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


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


def normalized_wallet_address(address: str) -> str:
    try:
        return normalize_wallet_address(address)
    except WalletError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def proof_hash_from_path(proof_hash: str) -> str:
    if proof_hash != proof_hash.strip():
        raise HTTPException(status_code=400, detail="proof hash must be 64 hex characters")
    clean = proof_hash.lower()
    if not HEX_HASH_RE.fullmatch(clean):
        raise HTTPException(status_code=400, detail="proof hash must be 64 hex characters")
    return clean


async def json_object(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid json body") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="json body must be an object")
    return data


def required_str(data: dict[str, Any], field: str) -> str:
    if field not in data or data[field] is None:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    value = data[field]
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    return value


def optional_str(data: dict[str, Any], field: str, default: str = "") -> str:
    value = data.get(field, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    return value


def parse_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        clean = value.strip()
        if clean and clean.lstrip("+-").isdigit():
            try:
                return int(clean)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{field} must be an integer") from exc
    raise HTTPException(status_code=400, detail=f"{field} must be an integer")


def required_int(data: dict[str, Any], field: str) -> int:
    value = data.get(field)
    if value is None:
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    return parse_int(value, field)


def optional_int(data: dict[str, Any], field: str, default: int) -> int:
    value = data.get(field, default)
    if value is None:
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    return parse_int(value, field)
