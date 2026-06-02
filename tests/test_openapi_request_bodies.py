from __future__ import annotations

from collections.abc import Iterable

from fastapi.testclient import TestClient

from app.main import create_app


def _post_schema(openapi: dict, path: str) -> dict:
    return openapi["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]


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
        schema.get("type") == "string" and schema.get("pattern") == "^[0-9]+$"
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


def test_attempt_openapi_request_bodies_remain_optional(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    attempt_body = openapi["paths"]["/api/v1/bounties/{bounty_id}/attempts"]["post"]["requestBody"]
    release_body = openapi["paths"]["/api/v1/bounty-attempts/{attempt_id}/release"]["post"][
        "requestBody"
    ]

    assert attempt_body.get("required") is not True
    assert release_body.get("required") is not True


def test_openapi_documents_auth_for_protected_routes(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    security_schemes = openapi["components"]["securitySchemes"]
    assert security_schemes["MergeWorkAdminToken"] == {
        "type": "apiKey",
        "in": "header",
        "name": "x-mergework-admin-token",
        "description": "Admin API token required for protected admin and treasury operations.",
    }
    assert security_schemes["MergeWorkGitHubSession"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "mrwk_user",
        "description": (
            "GitHub login session cookie from /auth/github/login required for "
            "authenticated contributor operations."
        ),
    }

    expected_auth_error = {
        "description": "Authentication required.",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"detail": {"type": "string"}},
                    "required": ["detail"],
                },
            },
        },
    }

    admin_routes = [
        ("/api/v1/admin/webhook-events", "get"),
        ("/api/v1/bounties", "post"),
        ("/api/v1/reconciliation/payouts", "get"),
        ("/api/v1/bounties/{bounty_id}/pay", "post"),
        ("/api/v1/bounties/{bounty_id}/close", "post"),
        ("/api/v1/treasury/proposals", "post"),
        ("/api/v1/treasury/proposals/{proposal_id}/execute", "post"),
    ]
    for path, method in admin_routes:
        operation = openapi["paths"][path][method]
        assert operation["security"] == [{"MergeWorkAdminToken": []}]
        assert operation["responses"]["401"] == expected_auth_error

    github_session_routes = [
        ("/api/v1/bounties/{bounty_id}/attempts", "post"),
        ("/api/v1/bounty-attempts/{attempt_id}/release", "post"),
        ("/api/v1/wallets/link-github", "post"),
        ("/api/v1/github/claim", "post"),
        ("/api/v1/treasury/proposals/{proposal_id}/challenges", "post"),
    ]
    for path, method in github_session_routes:
        operation = openapi["paths"][path][method]
        assert operation["security"] == [{"MergeWorkGitHubSession": []}]
        assert operation["responses"]["401"] == expected_auth_error

    public_operation = openapi["paths"]["/api/v1/bounties"]["get"]
    assert "security" not in public_operation
