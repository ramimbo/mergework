from __future__ import annotations

from typing import Any

JSON_CONTENT_TYPE = "application/json"

ACCOUNT_SCHEMA: dict[str, Any] = {"type": "string", "minLength": 1}
MRWK_ADDRESS_SCHEMA: dict[str, Any] = {
    "type": "string",
    "pattern": "^mrwk1[0-9a-f]{40}$",
}
NONNEGATIVE_INTEGER_SCHEMA: dict[str, Any] = {"type": "integer", "minimum": 0}
PUBLIC_URL_SCHEMA: dict[str, Any] = {"type": "string", "format": "uri"}
SIGNATURE_HEX_SCHEMA: dict[str, Any] = {"type": "string", "pattern": "^[0-9a-f]{128}$"}


def json_request_body(schema: dict[str, Any], *, required: bool = True) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": required,
            "content": {
                JSON_CONTENT_TYPE: {
                    "schema": schema,
                }
            },
        }
    }


ATTEMPT_REQUEST_BODY = json_request_body(
    {
        "type": "object",
        "properties": {
            "submitter_account": ACCOUNT_SCHEMA,
            "source_url": PUBLIC_URL_SCHEMA,
            "ttl_seconds": {
                "type": "integer",
                "minimum": 60,
                "maximum": 604800,
                "default": 86400,
            },
        },
    },
    required=False,
)

ATTEMPT_RELEASE_REQUEST_BODY = json_request_body(
    {
        "type": "object",
        "properties": {
            "submitter_account": ACCOUNT_SCHEMA,
        },
    },
    required=False,
)

WALLET_REGISTER_REQUEST_BODY = json_request_body(
    {
        "type": "object",
        "properties": {
            "public_key_hex": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "label": {"type": "string", "maxLength": 160},
        },
        "required": ["public_key_hex"],
    }
)

WALLET_AUTH_REQUEST_BODY = json_request_body(
    {
        "type": "object",
        "properties": {
            "address": MRWK_ADDRESS_SCHEMA,
            "nonce": NONNEGATIVE_INTEGER_SCHEMA,
            "signature_hex": SIGNATURE_HEX_SCHEMA,
        },
        "required": ["address", "nonce", "signature_hex"],
    }
)

WALLET_TRANSFER_REQUEST_BODY = json_request_body(
    {
        "type": "object",
        "properties": {
            "from_address": MRWK_ADDRESS_SCHEMA,
            "to_address": MRWK_ADDRESS_SCHEMA,
            "amount_mrwk": {
                "type": "string",
                "pattern": "^\\d+(?:\\.\\d{1,6})?$",
            },
            "nonce": NONNEGATIVE_INTEGER_SCHEMA,
            "memo": {"type": "string", "maxLength": 240, "default": ""},
            "signature_hex": SIGNATURE_HEX_SCHEMA,
        },
        "required": [
            "from_address",
            "to_address",
            "amount_mrwk",
            "nonce",
            "signature_hex",
        ],
    }
)

TREASURY_CHALLENGE_REQUEST_BODY = json_request_body(
    {
        "type": "object",
        "properties": {
            "challenge_type": {
                "type": "string",
                "enum": [
                    "bounty_not_open",
                    "duplicate_bounty",
                    "epoch_cap_exceeded",
                    "insufficient_reserve",
                    "subjective_note",
                    "submission_already_paid",
                ],
                "maxLength": 80,
            },
            "reason": {"type": "string", "maxLength": 1000},
        },
        "required": ["challenge_type", "reason"],
    }
)
