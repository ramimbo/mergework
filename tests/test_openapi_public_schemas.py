from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty
from app.main import create_app
from app.treasury import propose_treasury_action


def _schema_for(openapi: dict[str, Any], path: str) -> dict[str, Any]:
    schema = openapi["paths"][path]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert isinstance(schema, dict)
    return schema


def _ref_name(ref: str) -> str:
    return ref.removeprefix("#/components/schemas/")


def _resolved(openapi: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return openapi["components"]["schemas"][_ref_name(str(schema["$ref"]))]
    return schema


def _array_item_schema(openapi: dict[str, Any], path: str) -> dict[str, Any]:
    schema = _schema_for(openapi, path)
    assert schema["type"] == "array"
    item_schema = schema["items"]
    assert "$ref" in item_schema
    return _resolved(openapi, item_schema)


def _properties(openapi: dict[str, Any], path: str) -> dict[str, Any]:
    schema = _resolved(openapi, _schema_for(openapi, path))
    properties = schema["properties"]
    assert isinstance(properties, dict)
    return properties


def test_public_read_openapi_responses_expose_typed_fields(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    openapi = client.get("/openapi.json").json()

    status_props = _properties(openapi, "/api/v1/status")
    assert {
        "current_transfer_paths",
        "unsupported_public_paths",
        "future_path_boundary",
    } <= set(status_props)

    bounty_item = _array_item_schema(openapi, "/api/v1/bounties")
    bounty_props = bounty_item["properties"]
    assert {
        "effective_awards_remaining",
        "effective_available_mrwk",
        "pending_payout_awards",
        "pending_payout_proposals",
        "availability_state",
        "availability_note",
    } <= set(bounty_props)

    bounty_detail_props = _properties(openapi, "/api/v1/bounties/{bounty_id}")
    assert {"accepted_awards", "pending_close_proposal"} <= set(bounty_detail_props)

    summary_props = _properties(openapi, "/api/v1/bounties/summary")
    assert {"effective_open_awards", "effective_open_pool_mrwk"} <= set(summary_props)

    proposal_item = _array_item_schema(openapi, "/api/v1/treasury/proposals")
    proposal_props = proposal_item["properties"]
    assert {"payload_hash", "payload", "result", "challenges"} <= set(proposal_props)

    activity_props = _properties(openapi, "/api/v1/activity")
    assert {"totals", "contributors", "recent"} <= set(activity_props)

    accepted_work_props = _properties(openapi, "/api/v1/accounts/{account}/accepted-work")
    assert {"summary", "accepted_work"} <= set(accepted_work_props)

    proof_props = _properties(openapi, "/api/v1/proofs/{proof_hash}")
    assert {
        "kind",
        "bounty_id",
        "submission_url",
        "amount_mrwk",
        "ledger_sequence",
        "verifier_result",
    } <= set(proof_props)


def test_public_read_response_models_accept_seeded_payloads(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=688,
            issue_url="https://github.com/ramimbo/mergework/issues/688",
            title="Typed public read APIs",
            reward_mrwk="75",
            max_awards=2,
            acceptance="Public read endpoints should serialize typed payloads.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/699",
            accepted_by="maintainer",
            verifier_result={"source": "test", "accepted_by": "maintainer"},
        )
        propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": bounty.id,
                "to_account": "github:bob",
                "submission_url": "https://github.com/ramimbo/mergework/pull/700",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )
        bounty_id = bounty.id
        proof_hash = proof.hash

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    assert client.get("/api/v1/status").status_code == 200

    bounties_response = client.get("/api/v1/bounties")
    assert bounties_response.status_code == 200
    assert bounties_response.json()[0]["pending_payout_awards"] == 1

    bounty_response = client.get(f"/api/v1/bounties/{bounty_id}")
    assert bounty_response.status_code == 200
    assert bounty_response.json()["accepted_awards"][0]["proof_hash"] == proof_hash

    summary_response = client.get("/api/v1/bounties/summary")
    assert summary_response.status_code == 200
    assert summary_response.json()["effective_open_awards"] == 0

    proposals_response = client.get("/api/v1/treasury/proposals")
    assert proposals_response.status_code == 200
    assert proposals_response.json()[0]["result"] == {}
    assert proposals_response.json()[0]["challenges"] == []

    activity_response = client.get("/api/v1/activity")
    assert activity_response.status_code == 200
    assert activity_response.json()["totals"]["accepted_awards"] == 1

    account_response = client.get("/api/v1/accounts/github:alice/accepted-work")
    assert account_response.status_code == 200
    assert account_response.json()["summary"]["latest_proof_hash"] == proof_hash

    proof_response = client.get(f"/api/v1/proofs/{proof_hash}")
    assert proof_response.status_code == 200
    assert proof_response.json()["bounty_id"] == bounty_id
