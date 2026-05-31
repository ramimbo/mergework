from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import ensure_genesis
from app.main import create_app


def _post_request_body(openapi: dict[str, Any], path: str) -> dict[str, Any]:
    return openapi["paths"][path]["post"]["requestBody"]


def _json_schema(request_body: dict[str, Any]) -> dict[str, Any]:
    return request_body["content"]["application/json"]["schema"]


def test_openapi_documents_public_post_request_bodies(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    openapi = client.get("/openapi.json").json()

    attempt_body = _post_request_body(openapi, "/api/v1/bounties/{bounty_id}/attempts")
    attempt_schema = _json_schema(attempt_body)
    assert attempt_body["required"] is False
    assert set(attempt_schema["properties"]) == {
        "submitter_account",
        "source_url",
        "ttl_seconds",
    }
    assert attempt_schema["properties"]["ttl_seconds"]["maximum"] == 604800

    release_body = _post_request_body(openapi, "/api/v1/bounty-attempts/{attempt_id}/release")
    release_schema = _json_schema(release_body)
    assert release_body["required"] is False
    assert set(release_schema["properties"]) == {"submitter_account"}

    register_schema = _json_schema(_post_request_body(openapi, "/api/v1/wallets/register"))
    assert register_schema["required"] == ["public_key_hex"]
    assert register_schema["properties"]["public_key_hex"]["pattern"] == "^[0-9a-f]{64}$"
    assert register_schema["properties"]["label"]["maxLength"] == 160

    link_schema = _json_schema(_post_request_body(openapi, "/api/v1/wallets/link-github"))
    claim_schema = _json_schema(_post_request_body(openapi, "/api/v1/github/claim"))
    assert link_schema == claim_schema
    assert link_schema["required"] == ["address", "nonce", "signature_hex"]
    assert link_schema["properties"]["address"]["pattern"] == "^mrwk1[0-9a-f]{40}$"

    transfer_schema = _json_schema(_post_request_body(openapi, "/api/v1/transfers"))
    assert transfer_schema["required"] == [
        "from_address",
        "to_address",
        "amount_mrwk",
        "nonce",
        "signature_hex",
    ]
    assert transfer_schema["properties"]["amount_mrwk"]["pattern"] == "^\\d+(?:\\.\\d{1,6})?$"
    assert transfer_schema["properties"]["memo"]["maxLength"] == 240

    challenge_schema = _json_schema(
        _post_request_body(openapi, "/api/v1/treasury/proposals/{proposal_id}/challenges")
    )
    assert challenge_schema["required"] == ["challenge_type", "reason"]
    assert "subjective_note" in challenge_schema["properties"]["challenge_type"]["enum"]
    assert challenge_schema["properties"]["reason"]["maxLength"] == 1000
