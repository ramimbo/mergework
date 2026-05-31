from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app


def _post_request_body(openapi: dict[str, Any], path: str) -> dict[str, Any]:
    return openapi["paths"][path]["post"]["requestBody"]


def _json_schema(body: dict[str, Any]) -> dict[str, Any]:
    return body["content"]["application/json"]["schema"]


def test_openapi_exposes_attempt_request_body_schemas(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    create_body = _post_request_body(openapi, "/api/v1/bounties/{bounty_id}/attempts")
    create_schema = _json_schema(create_body)
    assert create_body["required"] is False
    assert set(create_schema["properties"]) == {
        "submitter_account",
        "source_url",
        "ttl_seconds",
    }
    assert create_schema["properties"]["ttl_seconds"]["minimum"] == 60
    assert create_schema["properties"]["ttl_seconds"]["maximum"] == 604800
    assert create_schema["properties"]["source_url"]["format"] == "uri"

    release_body = _post_request_body(openapi, "/api/v1/bounty-attempts/{attempt_id}/release")
    release_schema = _json_schema(release_body)
    assert release_body["required"] is False
    assert set(release_schema["properties"]) == {"submitter_account"}


def test_openapi_exposes_wallet_request_body_schemas(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    register_body = _post_request_body(openapi, "/api/v1/wallets/register")
    register_schema = _json_schema(register_body)
    assert register_body["required"] is True
    assert register_schema["required"] == ["public_key_hex"]
    assert register_schema["properties"]["public_key_hex"]["pattern"] == "^[0-9a-f]{64}$"
    assert register_schema["properties"]["label"]["maxLength"] == 160

    for path in ["/api/v1/wallets/link-github", "/api/v1/github/claim"]:
        body = _post_request_body(openapi, path)
        schema = _json_schema(body)
        assert body["required"] is True
        assert schema["required"] == ["address", "nonce", "signature_hex"]
        assert schema["properties"]["address"]["pattern"] == "^mrwk1[0-9a-f]{40}$"
        assert schema["properties"]["nonce"]["minimum"] == 1
        assert schema["properties"]["signature_hex"]["pattern"] == "^[0-9a-f]{128}$"

    transfer_body = _post_request_body(openapi, "/api/v1/transfers")
    transfer_schema = _json_schema(transfer_body)
    assert transfer_body["required"] is True
    assert transfer_schema["required"] == [
        "from_address",
        "to_address",
        "amount_mrwk",
        "nonce",
        "signature_hex",
    ]
    assert transfer_schema["properties"]["from_address"]["pattern"] == "^mrwk1[0-9a-f]{40}$"
    assert transfer_schema["properties"]["to_address"]["pattern"] == "^mrwk1[0-9a-f]{40}$"
    assert transfer_schema["properties"]["memo"]["maxLength"] == 240


def test_openapi_exposes_treasury_challenge_request_body_schema(
    sqlite_url: str,
) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    openapi = client.get("/openapi.json").json()

    body = _post_request_body(openapi, "/api/v1/treasury/proposals/{proposal_id}/challenges")
    schema = _json_schema(body)

    assert body["required"] is True
    assert schema["required"] == ["challenge_type", "reason"]
    assert "subjective_note" in schema["properties"]["challenge_type"]["enum"]
    assert "duplicate_bounty" in schema["properties"]["challenge_type"]["enum"]
    assert schema["properties"]["challenge_type"]["maxLength"] == 80
    assert schema["properties"]["reason"]["maxLength"] == 1000
