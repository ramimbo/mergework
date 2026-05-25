from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ADDRESS_RE = re.compile(r"^mrwk1[0-9a-f]{40}$")
PUBLIC_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
SIGNATURE_RE = re.compile(r"^[0-9a-f]{128}$")


class WalletError(ValueError):
    pass


def canonical_wallet_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _clean_string(value: object, message: str) -> str:
    if not isinstance(value, str):
        raise WalletError(message)
    return value.strip().lower()


def normalize_public_key_hex(public_key_hex: object) -> str:
    normalized = _clean_string(
        public_key_hex, "public key must be 32 bytes encoded as lowercase hex"
    )
    if not PUBLIC_KEY_RE.fullmatch(normalized):
        raise WalletError("public key must be 32 bytes encoded as lowercase hex")
    return normalized


def normalize_signature_hex(signature_hex: object) -> str:
    normalized = _clean_string(signature_hex, "signature must be 64 bytes encoded as lowercase hex")
    if not SIGNATURE_RE.fullmatch(normalized):
        raise WalletError("signature must be 64 bytes encoded as lowercase hex")
    return normalized


def normalize_wallet_address(address: object) -> str:
    normalized = _clean_string(address, "invalid MRWK wallet address")
    if not ADDRESS_RE.fullmatch(normalized):
        raise WalletError("invalid MRWK wallet address")
    return normalized


def address_from_public_key_hex(public_key_hex: object) -> str:
    public_key = normalize_public_key_hex(public_key_hex)
    digest = hashlib.sha256(bytes.fromhex(public_key)).hexdigest()
    return f"mrwk1{digest[:40]}"


def verify_wallet_signature(
    *, public_key_hex: object, payload: dict[str, Any], signature_hex: object
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
