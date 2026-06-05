from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.control_chars import contains_control_character

ADDRESS_RE = re.compile(r"^mrwk1[0-9a-f]{40}$")
PUBLIC_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
SIGNATURE_RE = re.compile(r"^[0-9a-f]{128}$")


class WalletError(ValueError):
    pass


def canonical_wallet_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _reject_control_characters(value: str, field: str) -> None:
    if contains_control_character(value):
        raise WalletError(f"{field} must not contain control characters")


def normalize_public_key_hex(public_key_hex: str) -> str:
    _reject_control_characters(public_key_hex, "public key")
    normalized = public_key_hex.strip().lower()
    if not PUBLIC_KEY_RE.fullmatch(normalized):
        raise WalletError("public key must be 32 bytes encoded as lowercase hex")
    return normalized


def normalize_signature_hex(signature_hex: str) -> str:
    _reject_control_characters(signature_hex, "signature")
    normalized = signature_hex.strip().lower()
    if not SIGNATURE_RE.fullmatch(normalized):
        raise WalletError("signature must be 64 bytes encoded as lowercase hex")
    return normalized


def normalize_wallet_address(address: str) -> str:
    _reject_control_characters(address, "MRWK wallet address")
    normalized = address.strip().lower()
    if not ADDRESS_RE.fullmatch(normalized):
        raise WalletError("invalid MRWK wallet address")
    return normalized


def address_from_public_key_hex(public_key_hex: str) -> str:
    public_key = normalize_public_key_hex(public_key_hex)
    digest = hashlib.sha256(bytes.fromhex(public_key)).hexdigest()
    return f"mrwk1{digest[:40]}"


def verify_wallet_signature(
    *, public_key_hex: str, payload: dict[str, Any], signature_hex: str
) -> bool:
    public_key = Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(normalize_public_key_hex(public_key_hex))
    )
    signature = bytes.fromhex(normalize_signature_hex(signature_hex))
    try:
        public_key.verify(signature, canonical_wallet_json(payload).encode())
    except InvalidSignature:
        return False
    return True
