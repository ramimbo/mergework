from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.accounts as accounts_module
import app.serializers as serializers_module
from app.accounts import (
    account_action_urls,
    account_api_context,
    account_page_context,
    normalized_account,
)
from app.db import create_schema, session_scope
from app.ledger.service import (
    TREASURY_ACCOUNT,
    add_ledger_entry,
    create_bounty,
    ensure_genesis,
    pay_bounty,
)
from app.main import create_app
from app.serializers import public_utc_timestamp
from app.treasury import propose_treasury_action


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
    assert page_context["account_action_urls"] == {
        "account_json": "/api/v1/accounts/github:alice",
        "accepted_work_json": "/api/v1/accounts/github:alice/accepted-work",
        "activity": "/api/v1/activity?account=github%3Aalice",
    }
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
    assert 'href="/api/v1/accounts/github:bob"' in page_response.text
    assert 'href="/api/v1/accounts/github:bob/accepted-work"' in page_response.text
    assert 'href="/api/v1/activity?account=github%3Abob"' in page_response.text
    assert '<p class="reference-cell">' in page_response.text
    assert f'href="/proofs/{proof.hash}"' in page_response.text


def test_account_action_urls_escape_query_accounts() -> None:
    assert account_action_urls("github:alice") == {
        "account_json": "/api/v1/accounts/github:alice",
        "accepted_work_json": "/api/v1/accounts/github:alice/accepted-work",
        "activity": "/api/v1/activity?account=github%3Aalice",
    }


def test_account_routes_expose_pending_payouts_separately_from_paid_work(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        pending_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=180,
            issue_url="https://github.com/ramimbo/mergework/issues/180",
            title="Pending account payout",
            reward_mrwk="75",
            acceptance="Pending payouts should be visible but not counted as paid.",
        )
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=181,
            issue_url="https://github.com/ramimbo/mergework/issues/181",
            title="Paid account payout",
            reward_mrwk="25",
            acceptance="Paid work remains proof-backed.",
        )
        proposal = propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": pending_bounty.id,
                "to_account": "github:alice",
                "submission_url": "https://github.com/ramimbo/mergework/pull/180",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )
        proof = pay_bounty(
            session,
            bounty_id=paid_bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/181",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    account_api = client.get("/api/v1/accounts/github:alice").json()
    accepted_api = client.get("/api/v1/accounts/github:alice/accepted-work").json()
    page = client.get("/accounts/github:alice").text

    assert account_api["balance_mrwk"] == "25"
    assert account_api["accepted_work"] == {
        "accepted_awards": 1,
        "accepted_mrwk": "25",
        "latest_ledger_sequence": proof.ledger_sequence,
        "latest_submission_url": "https://github.com/ramimbo/mergework/pull/181",
        "latest_proof_hash": proof.hash,
        "latest_proof_url": f"/proofs/{proof.hash}",
        "latest_proof_public_url": f"https://mrwk.online/proofs/{proof.hash}",
    }
    assert account_api["pending_summary"] == {
        "pending_awards": 1,
        "pending_mrwk": "75",
        "next_executes_after": public_utc_timestamp(proposal.executes_after),
    }
    assert accepted_api["summary"] == account_api["accepted_work"]
    assert accepted_api["pending_summary"] == account_api["pending_summary"]
    assert account_api["pending_payouts"] == accepted_api["pending_payouts"]
    assert accepted_api["pending_payouts"] == [
        {
            "proposal_id": proposal.id,
            "proposal_url": f"/api/v1/treasury/proposals/{proposal.id}",
            "status": "pending",
            "amount_mrwk": "75",
            "bounty_id": pending_bounty.id,
            "bounty_url": f"/bounties/{pending_bounty.id}",
            "repo": "ramimbo/mergework",
            "issue_number": 180,
            "issue_url": "https://github.com/ramimbo/mergework/issues/180",
            "submission_url": "https://github.com/ramimbo/mergework/pull/180",
            "accepted_by": "maintainer",
            "proposed_at": public_utc_timestamp(proposal.proposed_at),
            "executes_after": public_utc_timestamp(proposal.executes_after),
        }
    ]
    assert account_api["pending_summary"]["next_executes_after"].endswith("Z")
    assert len(accepted_api["accepted_work"]) == 1
    assert accepted_api["accepted_work"][0]["proof_hash"] == proof.hash
    assert accepted_api["accepted_work"][0]["created_at"].endswith("Z")
    assert "Pending payouts" in page
    assert "Accepted work queued for treasury execution, not proof-backed paid work." in page
    assert f'href="/api/v1/treasury/proposals/{proposal.id}"' in page
    assert "75 MRWK" in page
    assert "25 MRWK" in page
    assert "Â" not in page
    assert "&middot;" in page


def test_account_pages_fall_back_when_pending_payouts_fail(sqlite_url: str, monkeypatch) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    def fail_pending_payouts(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise RuntimeError("pending payout serializer unavailable")

    monkeypatch.setattr(serializers_module, "pending_payouts_for_account", fail_pending_payouts)

    with session_scope(sqlite_url) as session:
        api_context = account_api_context(session, "github:alice")
        page_context = account_page_context(session, "github:alice")

    assert api_context["pending_summary"] == {
        "pending_awards": 0,
        "pending_mrwk": "0",
        "next_executes_after": None,
    }
    assert api_context["pending_payouts"] == []
    assert page_context["pending_summary"] == api_context["pending_summary"]
    assert page_context["pending_payouts"] == []


def test_account_page_context_reuses_api_summary_context(sqlite_url: str, monkeypatch) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    calls = {"accepted": 0, "pending": 0}
    real_accepted_summary = accounts_module.safe_account_accepted_summary
    real_pending_payouts = accounts_module.safe_pending_payouts_for_account

    def count_accepted_summary(session: Session, account: str) -> dict[str, Any]:
        calls["accepted"] += 1
        return real_accepted_summary(session, account)

    def count_pending_payouts(session: Session, account: str) -> list[dict[str, Any]]:
        calls["pending"] += 1
        return real_pending_payouts(session, account)

    monkeypatch.setattr(accounts_module, "safe_account_accepted_summary", count_accepted_summary)
    monkeypatch.setattr(accounts_module, "safe_pending_payouts_for_account", count_pending_payouts)

    with session_scope(sqlite_url) as session:
        page_context = account_page_context(session, " GitHub:Alice ")

    assert calls == {"accepted": 1, "pending": 1}
    assert page_context["account"]["account"] == "github:alice"
    assert page_context["accepted_summary"] == page_context["account"]["accepted_work"]
    assert page_context["pending_summary"] == page_context["account"]["pending_summary"]
    assert page_context["pending_payouts"] == page_context["account"]["pending_payouts"]


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
    control = client.get("/accounts/github:alice?tx_type=%C2%85bounty_payment")
    masked_control = client.get("/accounts/github:alice?tx_type=%C2%85bounty_payment&tx_type=all")
    repeated = client.get("/accounts/github:alice?tx_type=bounty_payment&tx_type=all")

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

    assert control.status_code == 400
    assert control.json()["detail"] == "transaction type must not contain control characters"
    assert masked_control.status_code == 400
    assert masked_control.json()["detail"] == "transaction type must not contain control characters"
    assert repeated.status_code == 400
    assert repeated.json()["detail"] == "tx_type must be provided at most once"


def test_account_api_does_not_advertise_wallet_transfers_for_plain_accounts(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/api/v1/accounts/plain-account")

    assert response.status_code == 200
    assert response.json()["account"] == "plain-account"
    assert response.json()["transfer_status"] == (
        "MRWK wallet transfers require a registered mrwk1 address."
    )


def test_normalized_account_keeps_existing_account_validation_boundaries() -> None:
    assert normalized_account(" Reserve:Bounty:001 ") == "reserve:bounty:1"
    assert normalized_account("MRWK1" + ("A" * 40)) == "mrwk1" + ("a" * 40)
