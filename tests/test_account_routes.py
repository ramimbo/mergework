from __future__ import annotations

from fastapi.testclient import TestClient

from app.accounts import account_api_context, account_page_context, normalized_account
from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty
from app.main import create_app


def test_account_contexts_include_balance_status_and_proof_backed_rows(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=177,
            issue_url="https://github.com/ramimbo/mergework/issues/177",
            title="Account route extraction",
            reward_mrwk="40",
            acceptance="Account context should preserve accepted work and transaction rows.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/177",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        api_context = account_api_context(session, "GitHub:Alice")
        page_context = account_page_context(session, "github:alice")

    assert api_context["account"] == "github:alice"
    assert api_context["github_login"] == "alice"
    assert api_context["balance_mrwk"] == "40"
    assert api_context["transfer_status"].startswith("Claim GitHub balances")
    assert api_context["accepted_work"]["accepted_awards"] == 1
    assert api_context["accepted_work"]["latest_proof_hash"] == proof.hash

    assert page_context["account"]["account"] == "github:alice"
    assert page_context["accepted_summary"]["accepted_mrwk"] == "40"
    assert page_context["accepted_work"][0]["proof_hash"] == proof.hash
    assert page_context["transactions"][0]["proof_hash"] == proof.hash
    assert page_context["transactions"][0]["to"] == "github:alice"


def test_registered_account_routes_preserve_api_and_page_shapes(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=178,
            issue_url="https://github.com/ramimbo/mergework/issues/178",
            title="Account page route",
            reward_mrwk="25",
            acceptance="Account routes should render accepted work after extraction.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/178",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    api_response = client.get("/api/v1/accounts/GitHub:Bob")
    accepted_response = client.get("/api/v1/accounts/github:bob/accepted-work")
    page_response = client.get("/accounts/github:bob")

    assert api_response.status_code == 200
    assert api_response.json()["account"] == "github:bob"
    assert api_response.json()["accepted_work"]["latest_proof_hash"] == proof.hash
    assert accepted_response.status_code == 200
    assert accepted_response.json()["summary"]["accepted_mrwk"] == "25"
    assert accepted_response.json()["accepted_work"][0]["submission_url"].endswith("/pull/178")
    assert page_response.status_code == 200
    assert "github:bob" in page_response.text
    assert "25 MRWK" in page_response.text
    assert f'href="/proofs/{proof.hash}"' in page_response.text


def test_unknown_account_transfer_status_does_not_report_wallet_ready(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/api/v1/accounts/not-a-wallet")

    assert response.status_code == 200
    assert response.json()["exists"] is False
    assert response.json()["transfer_status"] == (
        "This account is not eligible for MRWK wallet transfers. Use a github:<login> "
        "account or registered mrwk1 wallet."
    )


def test_unregistered_wallet_transfer_status_requires_registration(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    account = "mrwk1" + ("0" * 40)

    response = client.get(f"/api/v1/accounts/{account}")

    assert response.status_code == 200
    assert response.json()["exists"] is False
    assert response.json()["transfer_status"] == (
        "Wallet address is not registered yet. Register this mrwk1 wallet before transfers."
    )


def test_normalized_account_keeps_existing_account_validation_boundaries() -> None:
    assert normalized_account(" Reserve:Bounty:001 ") == "reserve:bounty:1"
    assert normalized_account("MRWK1" + ("A" * 40)) == "mrwk1" + ("a" * 40)
