from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.accounts import (
    account_api_context,
    account_page_context,
    normalized_account,
    raw_account_api_path_account,
)
from app.db import create_schema, session_scope
from app.ledger.service import (
    MICRO_UNITS,
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
    assert f'href="/proofs/{proof.hash}"' in page_response.text


def test_account_transaction_links_encode_url_significant_account_ids(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        add_ledger_entry(
            session,
            entry_type="manual_adjustment",
            from_account="ops#source",
            to_account="github:alice",
            amount_microunits=MICRO_UNITS,
            reference="manual-adjustment",
        )
        add_ledger_entry(
            session,
            entry_type="manual_adjustment",
            from_account="slash/name",
            to_account="github:alice",
            amount_microunits=MICRO_UNITS,
            reference="manual-adjustment-slash",
        )
        add_ledger_entry(
            session,
            entry_type="manual_adjustment",
            from_account="foo/accepted-work",
            to_account="github:alice",
            amount_microunits=MICRO_UNITS,
            reference="manual-adjustment-reserved-suffix",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    account_page = client.get("/accounts/github:alice")
    encoded_account_page = client.get("/accounts/ops%23source")
    slash_account_page = client.get("/accounts/slash%2Fname")
    slash_account_api = client.get("/api/v1/accounts/slash%2Fname")
    slash_accepted_work_api = client.get("/api/v1/accounts/slash%2Fname/accepted-work")
    reserved_suffix_account_api = client.get("/api/v1/accounts/foo%2Faccepted-work")
    reserved_suffix_accepted_work_api = client.get(
        "/api/v1/accounts/foo%2Faccepted-work/accepted-work"
    )

    assert account_page.status_code == 200
    assert 'href="/accounts/ops%23source"' in account_page.text
    assert 'href="/accounts/ops#source"' not in account_page.text
    assert 'href="/accounts/slash%2Fname"' in account_page.text
    assert 'href="/accounts/slash/name"' not in account_page.text
    assert encoded_account_page.status_code == 200
    assert "ops#source" in encoded_account_page.text
    assert slash_account_page.status_code == 200
    assert "slash/name" in slash_account_page.text
    assert slash_account_api.status_code == 200
    assert slash_account_api.json()["account"] == "slash/name"
    assert slash_accepted_work_api.status_code == 200
    assert slash_accepted_work_api.json()["summary"]["accepted_awards"] == 0
    assert reserved_suffix_account_api.status_code == 200
    assert reserved_suffix_account_api.json()["account"] == "foo/accepted-work"
    assert "summary" not in reserved_suffix_account_api.json()
    assert reserved_suffix_accepted_work_api.status_code == 200
    assert reserved_suffix_accepted_work_api.json()["account"] == "foo/accepted-work"
    assert reserved_suffix_accepted_work_api.json()["summary"]["accepted_awards"] == 0


def test_normalized_account_keeps_existing_account_validation_boundaries() -> None:
    assert normalized_account(" Reserve:Bounty:001 ") == "reserve:bounty:1"
    assert normalized_account("MRWK1" + ("A" * 40)) == "mrwk1" + ("a" * 40)


def test_raw_account_api_path_rejects_malformed_utf8_encoded_account() -> None:
    request = type(
        "RequestDouble",
        (),
        {"scope": {"raw_path": b"/api/v1/accounts/%FF%2Faccepted-work"}},
    )()

    with pytest.raises(HTTPException) as exc_info:
        raw_account_api_path_account(request)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "account path must be valid UTF-8"
