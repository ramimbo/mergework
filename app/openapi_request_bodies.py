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

WORK_DISCOVERY_ACTION_SCHEMA = _object_schema(
    {
        "id": {"type": "string"},
        "required": {"type": "boolean"},
        "text": {"type": "string"},
    },
    required=["id", "required", "text"],
)

WORK_DISCOVERY_REQUIREMENTS_SCHEMA = _object_schema(
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
        "next_actions": {"type": "array", "items": WORK_DISCOVERY_ACTION_SCHEMA},
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

WORK_DISCOVERY_BOUNTY_SOURCE_URLS_SCHEMA = _object_schema(
    {
        "bounty": {"type": "string"},
        "attempts": {"type": "string"},
        "github_issue": {"type": "string", "format": "uri"},
    },
    required=["bounty", "attempts", "github_issue"],
)

WORK_DISCOVERY_PENDING_SOURCE_URLS_SCHEMA = _object_schema(
    {
        "proposal": {"type": "string"},
        "github_issue": {"type": "string", "format": "uri"},
    },
    required=["proposal", "github_issue"],
)

WORK_DISCOVERY_BOUNTY_ITEM_SCHEMA = _object_schema(
    {
        "availability_state": {
            "type": "string",
            "enum": ["live_bounty", "pending_payout", "closed_or_exhausted"],
        },
        "bounty_id": {"type": "integer", "minimum": 1},
        "issue_number": {"type": "integer", "minimum": 1},
        "title": {"type": "string"},
        "issue_url": {"type": "string", "format": "uri"},
        "reward_mrwk": MRWK_DECIMAL_SCHEMA,
        "max_awards": {"type": "integer", "minimum": 1},
        "effective_awards_remaining": {"type": "integer", "minimum": 0},
        "bounty_availability_state": {"type": "string"},
        "pending_payout_awards": {"type": "integer", "minimum": 0},
        "source_urls": WORK_DISCOVERY_BOUNTY_SOURCE_URLS_SCHEMA,
        "next_action": WORK_DISCOVERY_ACTION_SCHEMA,
        "submission_requirements": WORK_DISCOVERY_REQUIREMENTS_SCHEMA,
    },
    required=[
        "availability_state",
        "bounty_id",
        "issue_number",
        "title",
        "issue_url",
        "reward_mrwk",
        "max_awards",
        "effective_awards_remaining",
        "bounty_availability_state",
        "pending_payout_awards",
        "source_urls",
        "next_action",
        "submission_requirements",
    ],
)

WORK_DISCOVERY_PENDING_CREATE_ITEM_SCHEMA = _object_schema(
    {
        "availability_state": {"type": "string", "enum": ["pending_create"]},
        "proposal_id": {"type": "integer", "minimum": 1},
        "issue_number": {"type": "integer", "minimum": 1},
        "title": {"type": "string"},
        "issue_url": {"type": "string", "format": "uri"},
        "reward_mrwk": MRWK_DECIMAL_SCHEMA,
        "max_awards": {"type": "integer", "minimum": 1},
        "effective_awards_remaining": {"type": "integer", "minimum": 0},
        "executes_after": {"type": "string"},
        "source_urls": WORK_DISCOVERY_PENDING_SOURCE_URLS_SCHEMA,
        "next_action": WORK_DISCOVERY_ACTION_SCHEMA,
        "submission_requirements": WORK_DISCOVERY_REQUIREMENTS_SCHEMA,
    },
    required=[
        "availability_state",
        "proposal_id",
        "issue_number",
        "title",
        "issue_url",
        "reward_mrwk",
        "max_awards",
        "effective_awards_remaining",
        "executes_after",
        "source_urls",
        "next_action",
        "submission_requirements",
    ],
)

WORK_DISCOVERY_NON_CLAIMABLE_ISSUE_SCHEMA = _object_schema(
    {
        "availability_state": {"type": "string", "enum": ["proposed_work", "board_or_index"]},
        "repo": {"type": "string"},
        "issue_number": {"type": "integer", "minimum": 1},
        "issue_url": {"type": "string", "format": "uri"},
        "title": {"type": "string"},
        "note": {"type": "string"},
    },
    required=["availability_state", "note"],
)

WORK_DISCOVERY_RESPONSE_SCHEMA = _object_schema(
    {
        "type": {"type": "string", "enum": ["work_discovery"]},
        "summary": _object_schema(
            {
                "claimable_now_count": {"type": "integer", "minimum": 0},
                "opening_soon_count": {"type": "integer", "minimum": 0},
                "not_claimable_count": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1},
            },
            required=[
                "claimable_now_count",
                "opening_soon_count",
                "not_claimable_count",
                "limit",
            ],
        ),
        "state_definitions": {"type": "object", "additionalProperties": {"type": "string"}},
        "claimable_now": {"type": "array", "items": WORK_DISCOVERY_BOUNTY_ITEM_SCHEMA},
        "opening_soon": {"type": "array", "items": WORK_DISCOVERY_PENDING_CREATE_ITEM_SCHEMA},
        "not_claimable": {"type": "array", "items": WORK_DISCOVERY_BOUNTY_ITEM_SCHEMA},
        "non_claimable_issue_states": {
            "type": "array",
            "items": WORK_DISCOVERY_NON_CLAIMABLE_ISSUE_SCHEMA,
        },
    },
    required=[
        "type",
        "summary",
        "state_definitions",
        "claimable_now",
        "opening_soon",
        "not_claimable",
        "non_claimable_issue_states",
    ],
    description="Public read-only work discovery grouped by claimability.",
)

WORK_DISCOVERY_RESPONSE = {
    "responses": {
        "200": _json_response(WORK_DISCOVERY_RESPONSE_SCHEMA, description="Work discovery."),
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
