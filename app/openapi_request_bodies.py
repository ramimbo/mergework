from __future__ import annotations

from typing import Any


def _json_content(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "application/json": {
            "schema": schema,
        },
    }


def _request_body(schema: dict[str, Any], *, required: bool = True) -> dict[str, Any]:
    body: dict[str, Any] = {
        "content": _json_content(schema),
    }
    if required:
        body["required"] = True
    return body


def _json_response(
    schema: dict[str, Any], *, description: str = "Successful Response"
) -> dict[str, Any]:
    return {
        "description": description,
        "content": _json_content(schema),
    }


def _object_schema(
    properties: dict[str, Any], *, required: list[str] | None = None, description: str | None = None
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": True,
        "properties": properties,
    }
    if required:
        schema["required"] = required
    if description:
        schema["description"] = description
    return schema


INTEGER_OR_STRING_SCHEMA = {
    "anyOf": [
        {"type": "integer", "minimum": 1},
        {
            "type": "string",
            "description": "Positive integer value encoded as a string.",
            "pattern": "^[1-9][0-9]*$",
        },
    ],
}

MRWK_AMOUNT_SCHEMA = {
    "type": "string",
    "description": "Positive decimal MRWK amount with at most six decimal places.",
    "pattern": r"^(?=.*[1-9])\d+(?:\.\d{1,6})?$",
}

MRWK_DECIMAL_SCHEMA = {
    "type": "string",
    "description": "Decimal MRWK amount with at most six decimal places.",
    "pattern": r"^\d+(?:\.\d{1,6})?$",
}

BOUNDED_TTL_STRING_SCHEMA = {
    "type": "string",
    "description": "Integer value encoded as a string (60..604800).",
    "pattern": (
        r"^(?:[6-9][0-9]|[1-9][0-9]{2,4}|[1-5][0-9]{5}|"
        r"60[0-3][0-9]{3}|604[0-7][0-9]{2}|604800)$"
    ),
}

LOWERCASE_HEX_64_SCHEMA = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": "^[0-9a-f]{64}$",
}

LOWERCASE_HEX_128_SCHEMA = {
    "type": "string",
    "minLength": 128,
    "maxLength": 128,
    "pattern": "^[0-9a-f]{128}$",
}

MRWK_WALLET_ADDRESS_SCHEMA = {
    "type": "string",
    "description": "Registered MRWK wallet address in mrwk1 + 40 lowercase hex format.",
    "minLength": 45,
    "maxLength": 45,
    "pattern": "^mrwk1[0-9a-f]{40}$",
}

WALLET_LABEL_SCHEMA = {
    "type": "string",
    "description": "Optional wallet display label, trimmed and limited to 160 characters.",
    "maxLength": 160,
}

WALLET_MEMO_SCHEMA = {
    "type": "string",
    "description": "Transfer memo string, trimmed by the API and limited to 240 characters.",
    "maxLength": 240,
}

WALLET_RESPONSE_SCHEMA = _object_schema(
    {
        "address": MRWK_WALLET_ADDRESS_SCHEMA,
        "public_key_hex": LOWERCASE_HEX_64_SCHEMA,
        "label": {**WALLET_LABEL_SCHEMA, "nullable": True},
        "github_login": {"type": "string", "nullable": True},
        "balance_mrwk": MRWK_DECIMAL_SCHEMA,
        "nonce": {"type": "integer", "minimum": 0},
        "next_nonce": {"type": "integer", "minimum": 1},
        "created_at": {"type": "string"},
    }
)

LEDGER_ENTRY_RESPONSE_SCHEMA = _object_schema(
    {
        "sequence": {"type": "integer", "minimum": 1},
        "type": {"type": "string"},
        "from": {"type": "string", "nullable": True},
        "to": {"type": "string"},
        "amount_mrwk": MRWK_DECIMAL_SCHEMA,
        "reference": {"type": "string", "nullable": True},
        "previous_hash": {"type": "string", "nullable": True},
        "entry_hash": LOWERCASE_HEX_64_SCHEMA,
        "proof_hash": {**LOWERCASE_HEX_64_SCHEMA, "nullable": True},
        "created_at": {"type": "string"},
    }
)

WALLET_TRANSFER_RESPONSE_SCHEMA = _object_schema(
    {
        "hash": LOWERCASE_HEX_64_SCHEMA,
        "type": {"type": "string"},
        "ledger_sequence": {"type": "integer", "minimum": 1},
        "from_address": MRWK_WALLET_ADDRESS_SCHEMA,
        "to_address": MRWK_WALLET_ADDRESS_SCHEMA,
        "amount_mrwk": MRWK_AMOUNT_SCHEMA,
        "nonce": {"type": "integer", "minimum": 1},
        "memo": {"type": "string", "nullable": True},
        "created_at": {"type": "string"},
    }
)

BOUNTY_ATTEMPT_RESPONSE_SCHEMA = _object_schema(
    {
        "id": {"type": "integer", "minimum": 1},
        "bounty_id": {"type": "integer", "minimum": 1},
        "submitter_account": {"type": "string"},
        "source_url": {"type": "string", "nullable": True},
        "status": {"type": "string"},
        "expires_at": {"type": "string"},
        "created_at": {"type": "string"},
        "updated_at": {"type": "string"},
    }
)

ATTEMPT_REGISTRATION_RESPONSE_SCHEMA = _object_schema(
    {
        "status": {"type": "string"},
        "attempt": BOUNTY_ATTEMPT_RESPONSE_SCHEMA,
        "warnings": {"type": "array", "items": {"type": "string"}},
    }
)

ATTEMPT_CONFLICT_RESPONSE_SCHEMA = _object_schema(
    {
        "status": {"type": "string"},
        "bounty_id": {"type": "integer", "minimum": 1},
        "attempt": BOUNTY_ATTEMPT_RESPONSE_SCHEMA,
        "warnings": {"type": "array", "items": {"type": "string"}},
    }
)

ATTEMPT_RELEASE_RESPONSE_SCHEMA = _object_schema(
    {
        "status": {"type": "string"},
        "attempt": BOUNTY_ATTEMPT_RESPONSE_SCHEMA,
    }
)

TREASURY_CHALLENGE_RESPONSE_SCHEMA = _object_schema(
    {
        "id": {"type": "integer", "minimum": 1},
        "proposal_id": {"type": "integer", "minimum": 1},
        "challenger_account": {"type": "string"},
        "challenge_type": {"type": "string"},
        "status": {"type": "string"},
        "reason": {"type": "string"},
        "created_at": {"type": "string"},
    }
)

TREASURY_PROPOSAL_RESPONSE_SCHEMA = _object_schema(
    {
        "id": {"type": "integer", "minimum": 1},
        "type": {"type": "string"},
        "action": {"type": "string"},
        "status": {"type": "string"},
        "payload_hash": LOWERCASE_HEX_64_SCHEMA,
        "payload": {"type": "object", "additionalProperties": True},
        "proposed_by": {"type": "string"},
        "executed_by": {"type": "string", "nullable": True},
        "proposed_at": {"type": "string"},
        "executes_after": {"type": "string"},
        "executed_at": {"type": "string", "nullable": True},
        "executed_ledger_sequence": {"type": "integer", "nullable": True},
        "result": {"type": "object", "additionalProperties": True},
        "challenges": {"type": "array", "items": TREASURY_CHALLENGE_RESPONSE_SCHEMA},
    }
)

SUBMISSION_REQUIREMENTS_SCHEMA = _object_schema(
    {
        "submission_mode": {"type": "string"},
        "submission_url_kind": {"type": "string"},
        "expected_artifact": {"type": "string"},
        "attempt_endpoint_applicability": {"type": "string"},
        "reference_formats": {"type": "array", "items": {"type": "string"}},
        "claim_command": {"type": "string"},
        "attempt_endpoint": {"type": "string"},
        "evidence_required": {"type": "array", "items": {"type": "string"}},
        "acceptance_trigger": {"type": "string"},
        "public_metadata_must_avoid": {"type": "array", "items": {"type": "string"}},
        "next_actions": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
        },
    },
    required=[
        "submission_mode",
        "submission_url_kind",
        "expected_artifact",
        "attempt_endpoint_applicability",
        "reference_formats",
        "claim_command",
        "attempt_endpoint",
        "evidence_required",
        "acceptance_trigger",
        "public_metadata_must_avoid",
        "next_actions",
    ],
)

PENDING_PAYOUT_PROPOSAL_SCHEMA = _object_schema(
    {
        "proposal_id": {"type": "integer", "minimum": 1},
        "proposed_by": {"type": "string"},
        "proposed_at": {"type": "string"},
        "executes_after": {"type": "string"},
        "to_account": {"type": "string", "nullable": True},
        "submission_url": {"type": "string", "nullable": True},
        "accepted_by": {"type": "string", "nullable": True},
    },
    required=[
        "proposal_id",
        "proposed_by",
        "proposed_at",
        "executes_after",
        "to_account",
        "submission_url",
        "accepted_by",
    ],
)

PENDING_CLOSE_PROPOSAL_SCHEMA = _object_schema(
    {
        "proposal_id": {"type": "integer", "minimum": 1},
        "proposed_by": {"type": "string"},
        "proposed_at": {"type": "string"},
        "executes_after": {"type": "string"},
        "closed_by": {"type": "string", "nullable": True},
        "reference": {"type": "string", "nullable": True},
    },
    required=[
        "proposal_id",
        "proposed_by",
        "proposed_at",
        "executes_after",
        "closed_by",
        "reference",
    ],
)

BOUNTY_RESPONSE_REQUIRED_FIELDS = [
    "id",
    "repo",
    "issue_number",
    "issue_url",
    "title",
    "reward_mrwk",
    "available_mrwk",
    "reserved_mrwk",
    "max_awards",
    "awards_paid",
    "awards_remaining",
    "effective_available_mrwk",
    "effective_awards_remaining",
    "pending_payout_awards",
    "pending_payout_proposals",
    "pending_close_proposal",
    "availability_state",
    "availability_note",
    "submission_requirements",
    "status",
    "acceptance",
    "created_at",
    "active_attempt_count",
    "active_attempt_warnings",
    "attempt_endpoint",
]

BOUNTY_RESPONSE_SCHEMA = _object_schema(
    {
        "id": {"type": "integer", "minimum": 1},
        "repo": {"type": "string"},
        "issue_number": {"type": "integer", "minimum": 1},
        "issue_url": {"type": "string", "format": "uri"},
        "title": {"type": "string"},
        "reward_mrwk": MRWK_DECIMAL_SCHEMA,
        "available_mrwk": MRWK_DECIMAL_SCHEMA,
        "reserved_mrwk": MRWK_DECIMAL_SCHEMA,
        "max_awards": {"type": "integer", "minimum": 1},
        "awards_paid": {"type": "integer", "minimum": 0},
        "awards_remaining": {"type": "integer", "minimum": 0},
        "effective_available_mrwk": MRWK_DECIMAL_SCHEMA,
        "effective_awards_remaining": {"type": "integer", "minimum": 0},
        "pending_payout_awards": {"type": "integer", "minimum": 0},
        "pending_payout_proposals": {
            "type": "array",
            "items": PENDING_PAYOUT_PROPOSAL_SCHEMA,
        },
        "pending_close_proposal": {**PENDING_CLOSE_PROPOSAL_SCHEMA, "nullable": True},
        "availability_state": {
            "type": "string",
            "enum": [
                "open",
                "full",
                "pending_payouts_partial",
                "pending_payouts_full",
                "pending_close",
                "paid",
                "closed",
            ],
        },
        "availability_note": {"type": "string"},
        "submission_requirements": SUBMISSION_REQUIREMENTS_SCHEMA,
        "status": {"type": "string", "enum": ["open", "paid", "closed"]},
        "acceptance": {"type": "string"},
        "created_at": {"type": "string"},
        "active_attempt_count": {"type": "integer", "minimum": 0},
        "active_attempt_warnings": {"type": "array", "items": {"type": "string"}},
        "attempt_endpoint": {"type": "string"},
    },
    required=BOUNTY_RESPONSE_REQUIRED_FIELDS,
    description="Public bounty row returned by the bounty list and detail APIs.",
)

BOUNTY_ACCEPTED_AWARD_SCHEMA = _object_schema(
    {
        "proof_hash": LOWERCASE_HEX_64_SCHEMA,
        "proof_url": {"type": "string"},
        "ledger_sequence": {"type": "integer", "minimum": 1},
        "ledger_url": {"type": "string"},
        "account": {"type": "string", "nullable": True},
        "amount_mrwk": {**MRWK_DECIMAL_SCHEMA, "nullable": True},
        "submission_url": {"type": "string", "nullable": True},
        "accepted_by": {"type": "string", "nullable": True},
        "created_at": {"type": "string"},
    },
    required=[
        "proof_hash",
        "proof_url",
        "ledger_sequence",
        "ledger_url",
        "account",
        "amount_mrwk",
        "submission_url",
        "accepted_by",
        "created_at",
    ],
)

BOUNTY_DETAIL_RESPONSE_SCHEMA = {
    **BOUNTY_RESPONSE_SCHEMA,
    "properties": {
        **BOUNTY_RESPONSE_SCHEMA["properties"],
        "accepted_awards": {"type": "array", "items": BOUNTY_ACCEPTED_AWARD_SCHEMA},
    },
    "required": [*BOUNTY_RESPONSE_REQUIRED_FIELDS, "accepted_awards"],
    "description": "Public bounty detail row including proof-backed accepted awards.",
}

BOUNTY_LIST_RESPONSE = {
    "responses": {
        "200": _json_response(
            {"type": "array", "items": BOUNTY_RESPONSE_SCHEMA},
            description="Public bounty rows.",
        ),
    },
}

BOUNTY_DETAIL_RESPONSE = {
    "responses": {
        "200": _json_response(BOUNTY_DETAIL_RESPONSE_SCHEMA, description="Public bounty detail."),
    },
}

OPTIONAL_ATTEMPT_BODY = {
    "requestBody": _request_body(
        _object_schema(
            {
                "submitter_account": {
                    "type": "string",
                    "description": (
                        "Optional github:<login> account; must match the signed-in GitHub login."
                    ),
                },
                "source_url": {
                    "type": "string",
                    "format": "uri",
                    "description": "Optional public work branch or pull request URL.",
                },
                "ttl_seconds": {
                    "anyOf": [
                        {"type": "integer", "minimum": 60, "maximum": 604800},
                        BOUNDED_TTL_STRING_SCHEMA,
                    ],
                    "default": 86400,
                    "description": "Attempt lifetime in seconds, from 60 to 604800.",
                },
            },
            description="Optional advisory attempt reservation payload.",
        ),
        required=False,
    ),
    "responses": {
        "201": _json_response(
            ATTEMPT_REGISTRATION_RESPONSE_SCHEMA, description="Attempt registered."
        ),
        "409": _json_response(
            ATTEMPT_CONFLICT_RESPONSE_SCHEMA,
            description="Attempt unavailable or duplicate active attempt.",
        ),
    },
}

OPTIONAL_ATTEMPT_RELEASE_BODY = {
    "requestBody": _request_body(
        _object_schema(
            {
                "submitter_account": {
                    "type": "string",
                    "description": (
                        "Optional github:<login> account; must match the signed-in GitHub login."
                    ),
                },
            },
            description="Optional attempt release identity payload.",
        ),
        required=False,
    ),
    "responses": {
        "200": _json_response(ATTEMPT_RELEASE_RESPONSE_SCHEMA),
    },
}

WALLET_REGISTER_BODY = {
    "requestBody": _request_body(
        _object_schema(
            {
                "public_key_hex": {
                    **LOWERCASE_HEX_64_SCHEMA,
                    "description": "64-character lowercase hex Ed25519 public key.",
                },
                "label": WALLET_LABEL_SCHEMA,
            },
            required=["public_key_hex"],
        ),
    ),
    "responses": {
        "200": _json_response(WALLET_RESPONSE_SCHEMA),
    },
}

SIGNED_WALLET_ACTION_PROPERTIES = {
    "address": MRWK_WALLET_ADDRESS_SCHEMA,
    "nonce": INTEGER_OR_STRING_SCHEMA,
    "signature_hex": {
        **LOWERCASE_HEX_128_SCHEMA,
        "description": "128-character lowercase hex Ed25519 signature.",
    },
}

SIGNED_WALLET_ACTION_BODY = {
    "requestBody": _request_body(
        _object_schema(
            SIGNED_WALLET_ACTION_PROPERTIES,
            required=["address", "nonce", "signature_hex"],
        ),
    ),
    "responses": {
        "200": _json_response(WALLET_RESPONSE_SCHEMA),
    },
}

GITHUB_CLAIM_BODY = {
    "requestBody": SIGNED_WALLET_ACTION_BODY["requestBody"],
    "responses": {
        "200": _json_response(LEDGER_ENTRY_RESPONSE_SCHEMA),
    },
}

WALLET_TRANSFER_BODY = {
    "requestBody": _request_body(
        _object_schema(
            {
                "from_address": {
                    **MRWK_WALLET_ADDRESS_SCHEMA,
                    "description": "Sender registered mrwk1 wallet address.",
                },
                "to_address": {
                    **MRWK_WALLET_ADDRESS_SCHEMA,
                    "description": "Receiver registered mrwk1 wallet address.",
                },
                "amount_mrwk": MRWK_AMOUNT_SCHEMA,
                "nonce": INTEGER_OR_STRING_SCHEMA,
                "memo": WALLET_MEMO_SCHEMA,
                "signature_hex": {
                    **LOWERCASE_HEX_128_SCHEMA,
                    "description": "128-character lowercase hex Ed25519 signature.",
                },
            },
            required=["from_address", "to_address", "amount_mrwk", "nonce", "signature_hex"],
        ),
    ),
    "responses": {
        "200": _json_response(WALLET_TRANSFER_RESPONSE_SCHEMA),
    },
}

TREASURY_PROPOSAL_BODY = {
    "requestBody": _request_body(
        _object_schema(
            {
                "action": {"type": "string", "description": "Treasury action name."},
                "payload": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "Action-specific treasury proposal payload.",
                },
            },
            required=["action", "payload"],
        ),
    ),
    "responses": {
        "200": _json_response(TREASURY_PROPOSAL_RESPONSE_SCHEMA),
    },
}

TREASURY_CHALLENGE_BODY = {
    "requestBody": _request_body(
        _object_schema(
            {
                "challenge_type": {"type": "string", "description": "Challenge category."},
                "reason": {"type": "string", "description": "Public challenge reason."},
            },
            required=["challenge_type", "reason"],
        ),
    ),
    "responses": {
        "200": _json_response(TREASURY_CHALLENGE_RESPONSE_SCHEMA),
    },
}
