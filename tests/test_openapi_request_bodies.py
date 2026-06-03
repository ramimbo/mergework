from __future__ import annotations

import re
from collections.abc import Iterable

from fastapi.testclient import TestClient

from app.main import create_app

EXPECTED_TTL_STRING_PATTERN = (
    r"^(?:[6-9][0-9]|[1-9][0-9]{2,4}|[1-5][0-9]{5}|"
    r"60[0-3][0-9]{3}|604[0-7][0-9]{2}|604800)$"
)


def _post_schema(openapi: dict, path: str) -> dict:
    return openapi["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]


def _post_response_schema(openapi: dict, path: str, status: str = "200") -> dict:
    return openapi["paths"][path]["post"]["responses"][status]["content"]["application/json"][
        "schema"
    ]


def _assert_properties(schema: dict, expected: Iterable[str]) -> None:
    assert set(expected).issubset(schema["properties"])


def test_public_post_openapi_request_bodies_expose_expected_fields(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    expected_fields = {
        "/api/v1/bounties/{bounty_id}/attempts": {
            "submitter_account",
            "source_url",
            "ttl_seconds",
        },
        "/api/v1/bounty-attempts/{attempt_id}/release": {"submitter_account"},
        "/api/v1/wallets/register": {"public_key_hex", "label"},
        "/api/v1/wallets/link-github": {"address", "nonce", "signature_hex"},
        "/api/v1/github/claim": {"address", "nonce", "signature_hex"},
        "/api/v1/transfers": {
            "from_address",
            "to_address",
            "amount_mrwk",
            "nonce",
            "memo",
            "signature_hex",
        },
        "/api/v1/treasury/proposals": {"action", "payload"},
        "/api/v1/treasury/proposals/{proposal_id}/challenges": {"challenge_type", "reason"},
    }

    for path, fields in expected_fields.items():
        schema = _post_schema(openapi, path)
        assert schema["type"] == "object"
        _assert_properties(schema, fields)


def test_public_post_openapi_request_bodies_mark_required_fields(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    assert set(_post_schema(openapi, "/api/v1/wallets/register")["required"]) == {"public_key_hex"}
    assert set(_post_schema(openapi, "/api/v1/wallets/link-github")["required"]) == {
        "address",
        "nonce",
        "signature_hex",
    }
    assert set(_post_schema(openapi, "/api/v1/github/claim")["required"]) == {
        "address",
        "nonce",
        "signature_hex",
    }
    assert set(_post_schema(openapi, "/api/v1/transfers")["required"]) == {
        "from_address",
        "to_address",
        "amount_mrwk",
        "nonce",
        "signature_hex",
    }
    assert set(_post_schema(openapi, "/api/v1/treasury/proposals")["required"]) == {
        "action",
        "payload",
    }
    assert set(
        _post_schema(openapi, "/api/v1/treasury/proposals/{proposal_id}/challenges")["required"]
    ) == {"challenge_type", "reason"}


def test_public_post_openapi_request_bodies_publish_stable_constraints(
    sqlite_url: str,
) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    attempt_props = _post_schema(openapi, "/api/v1/bounties/{bounty_id}/attempts")["properties"]
    ttl_any_of = attempt_props["ttl_seconds"]["anyOf"]
    assert {"type": "integer", "minimum": 60, "maximum": 604800} in ttl_any_of
    assert any(
        schema.get("type") == "string" and schema.get("pattern") == EXPECTED_TTL_STRING_PATTERN
        for schema in ttl_any_of
    )

    wallet_props = _post_schema(openapi, "/api/v1/wallets/register")["properties"]
    assert wallet_props["public_key_hex"]["minLength"] == 64
    assert wallet_props["public_key_hex"]["maxLength"] == 64
    assert wallet_props["public_key_hex"]["pattern"] == "^[0-9a-f]{64}$"

    link_props = _post_schema(openapi, "/api/v1/wallets/link-github")["properties"]
    assert link_props["signature_hex"]["minLength"] == 128
    assert link_props["signature_hex"]["maxLength"] == 128
    assert link_props["signature_hex"]["pattern"] == "^[0-9a-f]{128}$"
    assert {"type": "integer", "minimum": 1} in link_props["nonce"]["anyOf"]

    transfer_props = _post_schema(openapi, "/api/v1/transfers")["properties"]
    assert transfer_props["signature_hex"]["pattern"] == "^[0-9a-f]{128}$"


def test_public_post_openapi_request_bodies_match_runtime_amount_and_ttl_bounds(
    sqlite_url: str,
) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    transfer_props = _post_schema(openapi, "/api/v1/transfers")["properties"]
    amount_schema = transfer_props["amount_mrwk"]
    assert amount_schema["pattern"] == r"^(?=.*[1-9])\d+(?:\.\d{1,6})?$"
    assert "positive" in amount_schema["description"].lower()
    assert "six" in amount_schema["description"].lower()

    attempt_props = _post_schema(openapi, "/api/v1/bounties/{bounty_id}/attempts")["properties"]
    ttl_string_schema = next(
        schema for schema in attempt_props["ttl_seconds"]["anyOf"] if schema.get("type") == "string"
    )
    assert ttl_string_schema["pattern"] != "^[0-9]+$"
    assert ttl_string_schema["pattern"] == EXPECTED_TTL_STRING_PATTERN


def test_public_post_openapi_ttl_string_pattern_matches_runtime_bounds(
    sqlite_url: str,
) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    attempt_props = _post_schema(openapi, "/api/v1/bounties/{bounty_id}/attempts")["properties"]
    ttl_string_schema = next(
        schema for schema in attempt_props["ttl_seconds"]["anyOf"] if schema.get("type") == "string"
    )
    pattern = ttl_string_schema["pattern"]

    for value in (
        "60",
        "99",
        "100",
        "99999",
        "100000",
        "599999",
        "600000",
        "603999",
        "604000",
        "604799",
        "604800",
    ):
        assert re.fullmatch(pattern, value), value

    for value in ("0", "59", "604801", "999999", "1000000", "5999999"):
        assert re.fullmatch(pattern, value) is None, value


def test_public_post_openapi_response_schemas_expose_wallet_transfer_and_attempt_fields(
    sqlite_url: str,
) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    wallet_schema = _post_response_schema(openapi, "/api/v1/wallets/register")
    _assert_properties(
        wallet_schema,
        {
            "address",
            "public_key_hex",
            "label",
            "github_login",
            "balance_mrwk",
            "nonce",
            "next_nonce",
            "created_at",
        },
    )
    link_wallet_schema = _post_response_schema(openapi, "/api/v1/wallets/link-github")
    _assert_properties(
        link_wallet_schema,
        {
            "address",
            "public_key_hex",
            "label",
            "github_login",
            "balance_mrwk",
            "nonce",
            "next_nonce",
            "created_at",
        },
    )

    claim_schema = _post_response_schema(openapi, "/api/v1/github/claim")
    _assert_properties(
        claim_schema,
        {
            "sequence",
            "type",
            "from",
            "to",
            "amount_mrwk",
            "reference",
            "previous_hash",
            "entry_hash",
            "proof_hash",
            "created_at",
        },
    )

    transfer_schema = _post_response_schema(openapi, "/api/v1/transfers")
    _assert_properties(
        transfer_schema,
        {
            "hash",
            "type",
            "ledger_sequence",
            "from_address",
            "to_address",
            "amount_mrwk",
            "nonce",
            "memo",
            "created_at",
        },
    )

    registered_attempt_schema = _post_response_schema(
        openapi, "/api/v1/bounties/{bounty_id}/attempts", "201"
    )
    _assert_properties(registered_attempt_schema, {"status", "attempt", "warnings"})
    _assert_properties(
        registered_attempt_schema["properties"]["attempt"],
        {
            "id",
            "bounty_id",
            "submitter_account",
            "source_url",
            "status",
            "expires_at",
            "created_at",
            "updated_at",
        },
    )

    conflict_schema = _post_response_schema(openapi, "/api/v1/bounties/{bounty_id}/attempts", "409")
    _assert_properties(conflict_schema, {"status", "attempt", "bounty_id", "warnings"})

    release_schema = _post_response_schema(openapi, "/api/v1/bounty-attempts/{attempt_id}/release")
    _assert_properties(release_schema, {"status", "attempt"})


def test_public_post_openapi_response_schemas_expose_treasury_fields(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    proposal_schema = _post_response_schema(openapi, "/api/v1/treasury/proposals")
    _assert_properties(
        proposal_schema,
        {
            "id",
            "type",
            "action",
            "status",
            "payload_hash",
            "payload",
            "proposed_by",
            "executed_by",
            "proposed_at",
            "executes_after",
            "executed_at",
            "executed_ledger_sequence",
            "result",
            "challenges",
        },
    )

    challenge_schema = _post_response_schema(
        openapi, "/api/v1/treasury/proposals/{proposal_id}/challenges"
    )
    _assert_properties(
        challenge_schema,
        {
            "id",
            "proposal_id",
            "challenger_account",
            "challenge_type",
            "status",
            "reason",
            "created_at",
        },
    )


def test_attempt_openapi_request_bodies_remain_optional(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    attempt_body = openapi["paths"]["/api/v1/bounties/{bounty_id}/attempts"]["post"]["requestBody"]
    release_body = openapi["paths"]["/api/v1/bounty-attempts/{attempt_id}/release"]["post"][
        "requestBody"
    ]

    assert attempt_body.get("required") is not True
    assert release_body.get("required") is not True
