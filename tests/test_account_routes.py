from __future__ import annotations

from fastapi.testclient import TestClient

from app.accounts import account_api_context, account_page_context, normalized_account
from app.db import create_schema, session_scope
from app.ledger.service import (
    TREASURY_ACCOUNT,
    add_ledger_entry,
    create_bounty,
    ensure_genesis,
    pay_bounty,
)
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
    assert '<p class="reference-cell">' in page_response.text
    assert f'href="/proofs/{proof.hash}"' in page_response.text


def test_account_page_filters_transactions_by_type(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=179,
            issue_url="https://github.com/ramimbo/mergework/issues/179",
            title="Account transaction filters",
            reward_mrwk="25",
            acceptance="Account pages should filter mixed transaction rows.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/179",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        claim = add_ledger_entry(
            session,
            entry_type="github_claim",
            from_account="github:alice",
            to_account="mrwk1" + ("a" * 40),
            amount_microunits=5_000_000,
            reference="github-claim:alice:mrwk:1",
        )
        add_ledger_entry(
            session,
            entry_type="test_funding",
            from_account=TREASURY_ACCOUNT,
            to_account="github:alice",
            amount_microunits=1_000_000,
            reference="test-funding:alice",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    all_rows = client.get("/accounts/github:alice")
    payments = client.get("/accounts/github:alice?tx_type=bounty_payment")
    claims = client.get("/accounts/github:alice?tx_type=github_claim")
    invalid = client.get("/accounts/github:alice?tx_type=bogus")

    assert all_rows.status_code == 200
    assert "Transaction type filters" in all_rows.text
    assert 'href="/accounts/github:alice?tx_type=bounty_payment"' in all_rows.text
    assert f'<td><a href="/ledger/{proof.ledger_sequence}">' in all_rows.text
    assert f'<td><a href="/ledger/{claim.sequence}">' in all_rows.text

    assert payments.status_code == 200
    assert 'tx_type=bounty_payment" aria-current="page"' in payments.text
    assert f'<td><a href="/ledger/{proof.ledger_sequence}">' in payments.text
    assert f'<td><a href="/ledger/{claim.sequence}">' not in payments.text

    assert claims.status_code == 200
    assert 'tx_type=github_claim" aria-current="page"' in claims.text
    assert f'<td><a href="/ledger/{claim.sequence}">' in claims.text
    assert f'<td><a href="/ledger/{proof.ledger_sequence}">' not in claims.text

    assert invalid.status_code == 400
    assert "transaction type must be one of" in invalid.text


def test_normalized_account_keeps_existing_account_validation_boundaries() -> None:
    assert normalized_account(" Reserve:Bounty:001 ") == "reserve:bounty:1"
    assert normalized_account("MRWK1" + ("A" * 40)) == "mrwk1" + ("a" * 40)
