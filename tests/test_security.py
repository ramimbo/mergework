from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.ledger.service as ledger_service
from app.db import create_schema, session_scope
from app.ledger.service import (
    TREASURY_ACCOUNT,
    LedgerError,
    add_ledger_entry,
    canonical_json,
    close_bounty,
    create_bounty,
    ensure_genesis,
    get_balance,
    parse_mrwk_amount,
    pay_bounty,
    public_url_or_none,
    register_wallet,
)
from app.main import _safe_next_path, _signed_value, create_app
from app.models import (
    Bounty,
    LedgerEntry,
    Proof,
    Submission,
    TreasuryProposal,
    WebhookEvent,
    utc_now,
)
from app.webhooks.github import handle_github_webhook


def _signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _admin_bounty_form_data(csrf_token: str | None = None) -> dict[str, str]:
    data = {
        "repo": "ramimbo/mergework",
        "issue_number": "77",
        "issue_url": "https://github.com/ramimbo/mergework/issues/77",
        "title": "Security hardening",
        "reward_mrwk": "10",
        "max_awards": "1",
        "acceptance": "Maintainer applies mrwk:accepted",
    }
    if csrf_token is not None:
        data["csrf_token"] = csrf_token
    return data


def _execute_treasury_proposal(client: TestClient, sqlite_url: str, proposal_id: int) -> Any:
    with session_scope(sqlite_url) as session:
        proposal = session.get(TreasuryProposal, proposal_id)
        assert proposal is not None
        proposal.executes_after = utc_now() - timedelta(seconds=1)
    return client.post(
        f"/api/v1/treasury/proposals/{proposal_id}/execute",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )


def test_browser_responses_set_security_headers(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


@pytest.mark.parametrize(
    ("path", "expected_asset_url"),
    (
        ("/api/docs", "https://cdn.jsdelivr.net/npm/swagger-ui-dist"),
        ("/api/redoc", "https://cdn.jsdelivr.net/npm/redoc"),
    ),
)
def test_api_docs_allow_external_assets_under_csp(
    sqlite_url: str, path: str, expected_asset_url: str
) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(path)

    assert response.status_code == 200
    assert expected_asset_url in response.text
    csp = response.headers["content-security-policy"]
    assert "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net" in csp
    assert (
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com"
    ) in csp
    assert ("img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly") in csp
    assert "font-src 'self' data: https://fonts.gstatic.com" in csp
    assert "worker-src 'self' blob:" in csp


def test_regular_pages_keep_strict_csp(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/")

    csp = response.headers["content-security-policy"]
    assert "https://cdn.jsdelivr.net" not in csp
    assert "'unsafe-inline'" not in csp


def test_admin_bounty_form_requires_csrf_for_cookie_auth(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MERGEWORK_ADMIN_LOGINS", "alice")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    client.cookies.set("mrwk_admin", _signed_value("alice", "test-cookie-secret"))

    page = client.get("/admin")
    token_match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)

    assert page.status_code == 200
    assert token_match is not None
    missing_token = client.post(
        "/admin/bounties", data=_admin_bounty_form_data(), follow_redirects=False
    )
    assert missing_token.status_code == 403

    created = client.post(
        "/admin/bounties",
        data=_admin_bounty_form_data(token_match.group(1)),
        follow_redirects=False,
    )
    assert created.status_code == 303


def test_admin_page_renders_safe_webhook_events_for_cookie_admin(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MERGEWORK_ADMIN_LOGINS", "alice")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    with session_scope(sqlite_url) as session:
        session.add(
            WebhookEvent(
                delivery_id="delivery-missing-submitter",
                event_type="pull_request",
                payload_hash="a" * 64,
                processed_status="missing_submitter",
            )
        )
        session.add(
            WebhookEvent(
                delivery_id="delivery-bounty-not-found",
                event_type="pull_request",
                payload_hash="b" * 64,
                processed_status="bounty_not_found",
            )
        )
        session.add(
            WebhookEvent(
                delivery_id="delivery-missing-submitter-uppercase",
                event_type="pull_request",
                payload_hash="d" * 64,
                processed_status="Missing_Submitter",
            )
        )
        session.add(
            WebhookEvent(
                delivery_id="delivery-exhausted-bounty",
                event_type="pull_request",
                payload_hash="e" * 64,
                processed_status="exhausted_bounty",
            )
        )
        session.add(
            WebhookEvent(
                delivery_id="delivery-paid-secret-payload-body",
                event_type="pull_request",
                payload_hash="c" * 64,
                processed_status="paid",
            )
        )

    unauthenticated = client.get("/admin", follow_redirects=False)
    client.cookies.set("mrwk_admin", _signed_value("alice", "test-cookie-secret"))
    all_events = client.get("/admin?webhook_limit=10")
    filtered = client.get("/admin?webhook_status= missing_submitter &webhook_limit=10")
    limited = client.get("/admin?webhook_limit=1")
    too_large = client.get("/admin?webhook_limit=201")

    assert unauthenticated.status_code == 302
    assert unauthenticated.headers["location"] == "/auth/github/login?next=/admin"
    assert all_events.status_code == 200
    assert "Treasury reserve capacity" in all_events.text
    assert "Available create reserve" in all_events.text
    assert "missing_submitter" in all_events.text
    assert "bounty_not_found" in all_events.text
    assert "exhausted_bounty" in all_events.text
    assert re.search(
        r"<code>missing_submitter</code>\s*</span>\s*<strong>2</strong>", all_events.text
    )
    summary_match = re.search(
        r'<div class="bounty-list-summary" aria-label="Webhook processed status summary">'
        r".*?</div>",
        all_events.text,
        flags=re.S,
    )
    assert summary_match is not None
    summary_html = summary_match.group(0)
    status_summary_positions = [
        summary_html.index("<code>missing_submitter</code>"),
        summary_html.index("<code>bounty_not_found</code>"),
        summary_html.index("<code>exhausted_bounty</code>"),
        summary_html.index("<code>paid</code>"),
    ]
    assert status_summary_positions == sorted(status_summary_positions)
    assert "paid" in all_events.text
    assert filtered.status_code == 200
    assert "delivery-missing-submitter" in filtered.text
    assert "delivery-missing-submitter-uppercase" in filtered.text
    assert "missing_submitter" in filtered.text
    assert "delivery-bounty-not-found" not in filtered.text
    assert "bounty_not_found" in filtered.text
    assert "a" * 64 in filtered.text
    assert "secret-payload-body" not in filtered.text
    assert limited.status_code == 200
    assert limited.text.count("<tr>") == 2
    assert too_large.status_code == 422


def test_admin_webhook_events_api_rejects_repeated_status_filter(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )

    response = client.get(
        "/api/v1/admin/webhook-events?status=missing_submitter&status=paid",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "status must be provided at most once"


def test_admin_page_calls_out_pending_create_proposals_when_they_limit_capacity(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MERGEWORK_ADMIN_LOGINS", "alice")
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    proposal = client.post(
        "/api/v1/bounties",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={
            **_admin_bounty_form_data(),
            "reward_mrwk": "10000",
            "max_awards": 1,
        },
    )
    client.cookies.set("mrwk_admin", _signed_value("alice", "test-cookie-secret"))

    response = client.get("/admin")

    assert proposal.status_code == 200
    assert response.status_code == 200
    assert re.search(
        r"<span>Available create reserve</span>\s*<strong>0 MRWK</strong>",
        response.text,
    )
    assert "Pending create-bounty proposals are using current create-bounty capacity." in (
        response.text
    )
    assert "Pending create executions do not free capacity immediately." in response.text
    assert "Projected capacity events" in response.text
    assert "No recent reserve entries are currently limiting create-bounty capacity." not in (
        response.text
    )


def test_admin_bounty_api_requires_admin_token_not_cookie_auth(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    monkeypatch.setenv("MERGEWORK_ADMIN_LOGINS", "alice")
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    client.cookies.set("mrwk_admin", _signed_value("alice", "test-cookie-secret"))

    cookie_only = client.post("/api/v1/bounties", json=_admin_bounty_form_data())
    token_auth = client.post(
        "/api/v1/bounties",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json=_admin_bounty_form_data(),
    )

    assert cookie_only.status_code == 401
    assert token_auth.status_code == 200


def test_admin_bounty_api_returns_400_for_malformed_json(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )

    non_object = client.post(
        "/api/v1/bounties",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json=["not", "an", "object"],
    )
    missing_field = client.post(
        "/api/v1/bounties",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={
            "issue_number": 77,
            "issue_url": "https://github.com/ramimbo/mergework/issues/77",
            "title": "Missing repo",
            "reward_mrwk": "10",
            "acceptance": "Maintainer applies mrwk:accepted",
        },
    )

    assert non_object.status_code == 400
    assert non_object.json()["detail"] == "json body must be an object"
    assert missing_field.status_code == 400
    assert missing_field.json()["detail"] == "repo is required"


def test_admin_bounty_api_rejects_duplicate_pending_repo_issue_proposal(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    payload = _admin_bounty_form_data()

    first = client.post(
        "/api/v1/bounties",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json=payload,
    )
    duplicate = client.post(
        "/api/v1/bounties",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json=payload,
    )

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "create_bounty proposal already pending"
    first_execution = _execute_treasury_proposal(client, sqlite_url, first.json()["id"])

    assert first_execution.status_code == 200
    assert first_execution.json()["result"]["bounty"]["issue_number"] == 77


def test_admin_bounty_api_rejects_fractional_integer_fields(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    payload = {
        "repo": "ramimbo/mergework",
        "issue_number": 77,
        "issue_url": "https://github.com/ramimbo/mergework/issues/77",
        "title": "Strict integer validation",
        "reward_mrwk": "10",
        "max_awards": 1,
        "acceptance": "Maintainer applies mrwk:accepted",
    }

    fractional_issue = client.post(
        "/api/v1/bounties",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={**payload, "issue_number": 77.9},
    )
    fractional_awards = client.post(
        "/api/v1/bounties",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={**payload, "max_awards": 1.5},
    )
    oversized_integer_issue = client.post(
        "/api/v1/bounties",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={
            **payload,
            "issue_number": 2**63,
            "issue_url": f"https://github.com/ramimbo/mergework/issues/{2**63}",
        },
    )
    oversized_string_issue = client.post(
        "/api/v1/bounties",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={**payload, "issue_number": "9" * 5000},
    )
    oversized_string_awards = client.post(
        "/api/v1/bounties",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={**payload, "max_awards": "9" * 5000},
    )

    assert fractional_issue.status_code == 400
    assert fractional_issue.json()["detail"] == "issue_number must be an integer"
    assert fractional_awards.status_code == 400
    assert fractional_awards.json()["detail"] == "max_awards must be an integer"
    assert oversized_integer_issue.status_code == 400
    assert oversized_integer_issue.json()["detail"] == "issue_number is too large"
    assert oversized_string_issue.status_code == 400
    assert oversized_string_issue.json()["detail"] == "issue_number must be an integer"
    assert oversized_string_awards.status_code == 400
    assert oversized_string_awards.json()["detail"] == "max_awards must be an integer"


def test_admin_webhook_events_api_lists_and_filters_processing_outcomes(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    with session_scope(sqlite_url) as session:
        session.add(
            WebhookEvent(
                delivery_id="delivery-paid",
                event_type="pull_request",
                payload_hash="a" * 64,
                processed_status="paid",
            )
        )
        session.add(
            WebhookEvent(
                delivery_id="delivery-missing",
                event_type="issues",
                payload_hash="b" * 64,
                processed_status="Missing_Submitter",
            )
        )

    unauthenticated = client.get("/api/v1/admin/webhook-events")
    all_events = client.get(
        "/api/v1/admin/webhook-events",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )
    filtered = client.get(
        "/api/v1/admin/webhook-events?status= Missing_Submitter ",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )
    limited = client.get(
        "/api/v1/admin/webhook-events?limit=1",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )
    too_large = client.get(
        "/api/v1/admin/webhook-events?limit=201",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )

    assert unauthenticated.status_code == 401
    assert all_events.status_code == 200
    all_by_delivery = {event["delivery_id"]: event for event in all_events.json()}
    assert set(all_by_delivery) == {"delivery-paid", "delivery-missing"}
    assert all_by_delivery["delivery-paid"]["payload_hash"] == "a" * 64
    assert filtered.status_code == 200
    assert filtered.json() == [
        {
            "delivery_id": "delivery-missing",
            "event_type": "issues",
            "processed_status": "Missing_Submitter",
            "payload_hash": "b" * 64,
            "created_at": filtered.json()[0]["created_at"],
        }
    ]
    assert limited.status_code == 200
    assert len(limited.json()) == 1
    assert too_large.status_code == 422


def test_admin_payout_api_requires_admin_token_not_cookie_auth(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    monkeypatch.setenv("MERGEWORK_ADMIN_LOGINS", "alice")
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=2,
            issue_url="https://github.com/ramimbo/mergework/issues/2",
            title="Star repo and verify wallet claim flow",
            reward_mrwk="25",
            acceptance="Contributor comments with wallet-flow proof.",
        )
        bounty_id = bounty.id
        wallet = register_wallet(session, public_key_hex="11" * 32, label="Contributor")
        wallet_address = wallet.address
    client.cookies.set("mrwk_admin", _signed_value("alice", "test-cookie-secret"))

    payload = {
        "to_account": wallet_address,
        "submission_url": "https://github.com/ramimbo/mergework/issues/2#issuecomment-1",
        "accepted_by": "alice",
    }
    cookie_only = client.post(f"/api/v1/bounties/{bounty_id}/pay", json=payload)
    token_auth = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json=payload,
    )

    assert cookie_only.status_code == 401
    assert token_auth.status_code == 200
    assert token_auth.json()["action"] == "pay_bounty"
    assert token_auth.json()["payload"]["to_account"] == wallet_address
    with session_scope(sqlite_url) as session:
        assert get_balance(session, wallet_address) == 0

    executed = _execute_treasury_proposal(client, sqlite_url, token_auth.json()["id"])

    assert executed.status_code == 200
    payout = executed.json()["result"]["payout"]
    assert payout["to_account"] == wallet_address
    assert payout["submission_id"] is not None
    assert payout["ledger_sequence"] is not None
    assert payout["ledger_url"].startswith("/ledger/")
    assert payout["proof_url"].startswith("/proofs/")
    with session_scope(sqlite_url) as session:
        assert get_balance(session, wallet_address) == 25_000_000


def test_admin_payout_api_returns_existing_proof_for_duplicate_submission(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=283,
            issue_url="https://github.com/ramimbo/mergework/issues/283",
            title="Webhook and admin payout observability",
            reward_mrwk="15",
            max_awards=2,
            acceptance="Maintainer needs visible payout state and duplicate protection.",
        )
        bounty_id = bounty.id

    headers = {"x-mergework-admin-token": "admin-token-for-tests"}
    first_payload = {
        "to_account": "github:alice",
        "submission_url": "https://github.com/ramimbo/mergework/pull/283",
        "accepted_by": "maintainer",
    }
    first = client.post(f"/api/v1/bounties/{bounty_id}/pay", headers=headers, json=first_payload)

    assert first.status_code == 200
    first_proposal = first.json()
    assert first_proposal["action"] == "pay_bounty"
    first_execution = _execute_treasury_proposal(client, sqlite_url, first_proposal["id"])
    assert first_execution.status_code == 200
    first_body = first_execution.json()["result"]["payout"]
    assert first_body["status"] == "paid"
    assert first_body["bounty_id"] == bounty_id
    assert first_body["to_account"] == "github:alice"
    assert first_body["submission_url"] == "https://github.com/ramimbo/mergework/pull/283"
    assert first_body["proof_hash"]
    assert first_body["proof_url"] == f"/proofs/{first_body['proof_hash']}"
    assert first_body["ledger_url"] == f"/ledger/{first_body['ledger_sequence']}"
    assert first_body["submission_id"] is not None
    assert isinstance(first_body["ledger_sequence"], int)

    duplicate = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers=headers,
        json={**first_payload, "to_account": "github:carol"},
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["status"] == "already_paid"
    assert duplicate.json()["proof_hash"] == first_body["proof_hash"]
    assert duplicate.json()["ledger_sequence"] == first_body["ledger_sequence"]
    assert duplicate.json()["submission_id"] == first_body["submission_id"]
    assert duplicate.json()["submission_url"] == first_body["submission_url"]
    with session_scope(sqlite_url) as session:
        bounty = session.get(Bounty, bounty_id)
        payments = session.scalars(
            select(LedgerEntry).where(LedgerEntry.entry_type == "bounty_payment")
        ).all()
        assert bounty is not None
        assert bounty.awards_paid == 1
        assert bounty.status == "open"
        assert len(payments) == 1
        assert get_balance(session, "github:alice") == 15_000_000

    final = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers=headers,
        json={
            "to_account": "github:bob",
            "submission_url": "https://github.com/ramimbo/mergework/pull/284",
            "accepted_by": "maintainer",
        },
    )

    assert final.status_code == 200
    final_execution = _execute_treasury_proposal(client, sqlite_url, final.json()["id"])

    assert final_execution.status_code == 200
    final_body = final_execution.json()["result"]["payout"]
    assert final_body["status"] == "paid"
    assert final_body["bounty_status"] == "paid"
    assert final_body["awards_paid"] == 2
    assert final_body["awards_remaining"] == 0

    paid_duplicate = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers=headers,
        json={
            "to_account": "github:carol",
            "submission_url": "https://github.com/ramimbo/mergework/pull/284",
            "accepted_by": "maintainer",
        },
    )

    assert paid_duplicate.status_code == 409
    assert paid_duplicate.json()["status"] == "already_paid"
    assert paid_duplicate.json()["proof_hash"] == final_body["proof_hash"]
    assert paid_duplicate.json()["ledger_sequence"] == final_body["ledger_sequence"]
    assert paid_duplicate.json()["submission_id"] == final_body["submission_id"]
    with session_scope(sqlite_url) as session:
        bounty = session.get(Bounty, bounty_id)
        payments = session.scalars(
            select(LedgerEntry).where(LedgerEntry.entry_type == "bounty_payment")
        ).all()
        assert bounty is not None
        assert bounty.status == "paid"
        assert bounty.awards_paid == 2
        assert bounty.max_awards - bounty.awards_paid == 0
        assert len(payments) == 2
        assert get_balance(session, "github:bob") == 15_000_000
        assert get_balance(session, "github:carol") == 0


def test_admin_payout_reconciliation_api_reports_missing_and_duplicate_evidence(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        missing_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=281,
            issue_url="https://github.com/ramimbo/mergework/issues/281",
            title="Reconcile missing payout",
            reward_mrwk="15",
            acceptance="Maintainer needs accepted-but-unpaid work surfaced.",
        )
        duplicate_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=282,
            issue_url="https://github.com/ramimbo/mergework/issues/282",
            title="Reconcile duplicate payout evidence",
            reward_mrwk="15",
            acceptance="Maintainer needs duplicate payment evidence surfaced.",
        )
        missing_submission = Submission(
            bounty_id=missing_bounty.id,
            submitter_account="github:alice",
            url="https://github.com/ramimbo/mergework/pull/281",
            status="accepted",
            verifier_result=canonical_json({"label": "mrwk:accepted"}),
        )
        session.add(missing_submission)
        proof = pay_bounty(
            session,
            bounty_id=duplicate_bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/282",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        session.add(
            Proof(
                hash="e" * 64,
                ledger_sequence=proof.ledger_sequence,
                bounty_id=duplicate_bounty.id,
                submission_id=proof.submission_id,
                kind="bounty_payment",
                public_json=proof.public_json,
            )
        )

    unauthorized = client.get("/api/v1/reconciliation/payouts")
    response = client.get(
        "/api/v1/reconciliation/payouts",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_by"] == "api-token"
    assert payload["summary"] == {
        "accepted_submissions": 2,
        "paid": 0,
        "missing_payment": 1,
        "duplicate_payment_evidence": 1,
        "mismatched_payment_evidence": 0,
    }
    by_status = {check["status"]: check for check in payload["checks"]}
    assert by_status["missing_payment"]["submission_url"] == (
        "https://github.com/ramimbo/mergework/pull/281"
    )
    assert by_status["missing_payment"]["evidence"] == []
    duplicate_check = by_status["duplicate_payment_evidence"]
    assert duplicate_check["submission_url"] == "https://github.com/ramimbo/mergework/pull/282"
    assert duplicate_check["evidence_count"] == 2
    assert {evidence["proof_url"] for evidence in duplicate_check["evidence"]} == {
        f"/proofs/{proof.hash}",
        f"/proofs/{'e' * 64}",
    }
    for evidence in duplicate_check["evidence"]:
        assert isinstance(evidence["ledger_sequence"], int)
        assert evidence["ledger_sequence"] == proof.ledger_sequence
        assert evidence["ledger_type"] == "bounty_payment"
        assert evidence["reference"] == "https://github.com/ramimbo/mergework/pull/282"
        assert evidence["to_account"] == "github:bob"
        assert evidence["amount_mrwk"] == "15"
        assert evidence["matches_submission"] is True


def test_admin_payout_api_returns_400_for_malformed_json(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
        raise_server_exceptions=False,
    )
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=3,
            issue_url="https://github.com/ramimbo/mergework/issues/3",
            title="Malformed payout JSON",
            reward_mrwk="25",
            acceptance="Maintainer verifies payout.",
        )
        bounty_id = bounty.id

    invalid_json = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        content="{",
        headers={
            "x-mergework-admin-token": "admin-token-for-tests",
            "content-type": "application/json",
        },
    )

    assert invalid_json.status_code == 400
    assert invalid_json.json()["detail"] == "invalid json body"


def test_admin_payout_api_rejects_non_string_metadata_fields(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        accepted_by_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=122,
            issue_url="https://github.com/ramimbo/mergework/issues/122",
            title="Strict payout accepted_by metadata",
            reward_mrwk="25",
            acceptance="Maintainer verifies payout metadata.",
        )
        note_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=123,
            issue_url="https://github.com/ramimbo/mergework/issues/123",
            title="Strict payout note metadata",
            reward_mrwk="25",
            acceptance="Maintainer verifies payout metadata.",
        )

    accepted_by = client.post(
        f"/api/v1/bounties/{accepted_by_bounty.id}/pay",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={
            "to_account": "github:alice",
            "submission_url": "https://github.com/ramimbo/mergework/pull/122",
            "accepted_by": 123,
        },
    )
    note = client.post(
        f"/api/v1/bounties/{note_bounty.id}/pay",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={
            "to_account": "github:bob",
            "submission_url": "https://github.com/ramimbo/mergework/pull/123",
            "note": ["not", "text"],
        },
    )

    assert accepted_by.status_code == 400
    assert accepted_by.json()["detail"] == "accepted_by must be a string"
    assert note.status_code == 400
    assert note.json()["detail"] == "note must be a string"


def test_admin_close_bounty_api_releases_remaining_reserve(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=89,
            issue_url="https://github.com/ramimbo/mergework/issues/89",
            title="Close stale bounty",
            reward_mrwk="25",
            max_awards=2,
            acceptance="Each accepted submission earns one award.",
        )
        bounty_id = bounty.id

    unauthenticated = client.post(f"/api/v1/bounties/{bounty_id}/close", json={})
    token_auth = client.post(
        f"/api/v1/bounties/{bounty_id}/close",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={"reference": "https://github.com/ramimbo/mergework/issues/89#close"},
    )

    assert unauthenticated.status_code == 401
    assert token_auth.status_code == 200
    assert token_auth.json()["action"] == "close_bounty"
    assert token_auth.json()["status"] == "pending"

    executed = _execute_treasury_proposal(client, sqlite_url, token_auth.json()["id"])

    assert executed.status_code == 200
    assert executed.json()["result"]["close"]["status"] == "closed"
    assert executed.json()["result"]["close"]["released_mrwk"] == "50"


def test_admin_bounty_id_routes_reject_non_positive_ids(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    headers = {"x-mergework-admin-token": "admin-token-for-tests"}

    payout = client.post(
        "/api/v1/bounties/0/pay",
        headers=headers,
        json={
            "to_account": "github:alice",
            "submission_url": "https://github.com/ramimbo/mergework/pull/1",
        },
    )
    close = client.post("/api/v1/bounties/0/close", headers=headers, json={})

    assert payout.status_code == 400
    assert payout.json()["detail"] == "bounty id must be positive"
    assert close.status_code == 400
    assert close.json()["detail"] == "bounty id must be positive"


def test_admin_bounty_api_accepts_multi_award_count(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )

    response = client.post(
        "/api/v1/bounties",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={
            "repo": "ramimbo/mergework",
            "issue_number": 88,
            "issue_url": "https://github.com/ramimbo/mergework/issues/88",
            "title": "Multi-award admin bounty",
            "reward_mrwk": "25",
            "max_awards": 3,
            "acceptance": "Each accepted submission earns one award.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "create_bounty"
    assert body["status"] == "pending"
    executed = _execute_treasury_proposal(client, sqlite_url, body["id"])

    assert executed.status_code == 200
    bounty = executed.json()["result"]["bounty"]
    assert bounty["reward_mrwk"] == "25"
    assert bounty["reserved_mrwk"] == "75"
    assert bounty["max_awards"] == 3
    assert bounty["awards_remaining"] == 3


@pytest.mark.parametrize(
    ("next_path", "expected"),
    [
        (None, "/me"),
        ("", "/me"),
        ("https://evil.example/me", "/me"),
        ("//evil.example/me", "/me"),
        ("/\\evil.example/me", "/me"),
        ("/me\nLocation: https://evil.example", "/me"),
        ("/me%0aLocation:%20https://evil.example", "/me"),
        ("/me" + chr(0x85), "/me"),
        ("/me\x7f", "/me"),
        ("/" + ("a" * 2048), "/me"),
        ("/me", "/me"),
        ("/bounties?status=open", "/bounties?status=open"),
    ],
)
def test_oauth_next_path_rejects_external_or_headerlike_paths(
    next_path: str | None, expected: str
) -> None:
    assert _safe_next_path(next_path) == expected


def test_amount_parser_rejects_non_finite_values() -> None:
    for amount in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(LedgerError, match="invalid MRWK amount"):
            parse_mrwk_amount(amount)


def test_amount_parser_rejects_non_decimal_notation() -> None:
    for amount in ("1e3", "1E-3", "+1"):
        with pytest.raises(LedgerError, match="invalid MRWK amount"):
            parse_mrwk_amount(amount)

    assert parse_mrwk_amount("1.5") == 1_500_000
    with pytest.raises(LedgerError, match="amount must be positive"):
        parse_mrwk_amount("-1")


@pytest.mark.parametrize("amount", ("\t1", "1\n", "1\r", "1\x85"))
def test_amount_parser_rejects_control_characters(amount: str) -> None:
    with pytest.raises(LedgerError, match="invalid MRWK amount"):
        parse_mrwk_amount(amount)


@pytest.mark.parametrize("amount", ("1.0000000", "0.0000010", "0.1000000"))
def test_amount_parser_rejects_more_than_six_decimal_places(amount: str) -> None:
    with pytest.raises(LedgerError, match="MRWK supports at most 6 decimal places"):
        parse_mrwk_amount(amount)


def test_amount_parser_rejects_values_above_fixed_supply() -> None:
    with pytest.raises(LedgerError, match="amount exceeds fixed supply"):
        parse_mrwk_amount("100000001")


def test_bounty_payment_proof_rejects_control_character_metadata(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        first_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=15,
            issue_url="https://github.com/ramimbo/mergework/issues/15",
            title="Proof metadata",
            reward_mrwk="1",
            acceptance="Maintainer applies mrwk:accepted",
        )
        second_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=16,
            issue_url="https://github.com/ramimbo/mergework/issues/16",
            title="Proof verifier metadata",
            reward_mrwk="1",
            acceptance="Maintainer applies mrwk:accepted",
        )

        with pytest.raises(LedgerError, match="accepted_by must not contain control characters"):
            pay_bounty(
                session,
                bounty_id=first_bounty.id,
                to_account="github:alice",
                submission_url="https://github.com/ramimbo/mergework/pull/15",
                accepted_by="maintainer\nops",
                verifier_result={"label": "mrwk:accepted"},
            )
        with pytest.raises(
            LedgerError, match="verifier_result.note must not contain control characters"
        ):
            pay_bounty(
                session,
                bounty_id=second_bounty.id,
                to_account="github:bob",
                submission_url="https://github.com/ramimbo/mergework/pull/16",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted", "note": "line1\nline2"},
            )

        assert first_bounty.awards_paid == 0
        assert second_bounty.awards_paid == 0
        assert get_balance(session, "github:alice") == 0
        assert get_balance(session, "github:bob") == 0


def test_admin_payout_api_rejects_control_character_note(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=17,
            issue_url="https://github.com/ramimbo/mergework/issues/17",
            title="Admin proof metadata",
            reward_mrwk="25",
            acceptance="Maintainer verifies payout.",
        )
        bounty_id = bounty.id

    response = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={
            "to_account": "github:alice",
            "submission_url": "https://github.com/ramimbo/mergework/pull/17",
            "accepted_by": "maintainer",
            "note": "line1\nline2",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == ("verifier_result.note must not contain control characters")
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:alice") == 0


def test_admin_payout_api_omits_blank_note_from_public_proof(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=18,
            issue_url="https://github.com/ramimbo/mergework/issues/18",
            title="Admin blank proof note",
            reward_mrwk="25",
            acceptance="Maintainer verifies payout.",
        )
        bounty_id = bounty.id

    response = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={
            "to_account": "github:alice",
            "submission_url": "https://github.com/ramimbo/mergework/pull/18",
            "accepted_by": "maintainer",
            "note": "   ",
        },
    )

    assert response.status_code == 200
    executed = _execute_treasury_proposal(client, sqlite_url, response.json()["id"])

    assert executed.status_code == 200
    with session_scope(sqlite_url) as session:
        proof = session.scalars(select(Proof)).one()
        verifier_result = json.loads(proof.public_json)["verifier_result"]
        assert verifier_result == {"accepted_by": "maintainer", "source": "treasury_proposal"}
        assert get_balance(session, "github:alice") == 25_000_000


def test_admin_payout_api_trims_note_in_public_proof(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=19,
            issue_url="https://github.com/ramimbo/mergework/issues/19",
            title="Admin trimmed proof note",
            reward_mrwk="25",
            acceptance="Maintainer verifies payout.",
        )
        bounty_id = bounty.id

    response = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
        json={
            "to_account": "github:bob",
            "submission_url": "https://github.com/ramimbo/mergework/pull/19",
            "accepted_by": "maintainer",
            "note": "  verified manually  ",
        },
    )

    assert response.status_code == 200
    executed = _execute_treasury_proposal(client, sqlite_url, response.json()["id"])

    assert executed.status_code == 200
    with session_scope(sqlite_url) as session:
        proof = session.scalars(select(Proof)).one()
        verifier_result = json.loads(proof.public_json)["verifier_result"]
        assert verifier_result["note"] == "verified manually"
        assert get_balance(session, "github:bob") == 25_000_000


def test_bounty_urls_reject_control_characters(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        with pytest.raises(LedgerError, match="URL must not contain control characters"):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=11,
                issue_url="https://github.com/ramimbo/mergework/issues/11\nextra",
                title="Control URL",
                reward_mrwk="1",
                acceptance="Maintainer applies mrwk:accepted",
            )
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=12,
            issue_url="https://github.com/ramimbo/mergework/issues/12",
            title="Safe URL",
            reward_mrwk="1",
            acceptance="Maintainer applies mrwk:accepted",
        )
        with pytest.raises(LedgerError, match="URL must not contain control characters"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:alice",
                submission_url="https://github.com/ramimbo/mergework/pull/12\textra",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
            )


def test_close_bounty_rejects_control_character_reference(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=13,
            issue_url="https://github.com/ramimbo/mergework/issues/13",
            title="Close reference guard",
            reward_mrwk="1",
            acceptance="Maintainer applies mrwk:accepted",
        )

        with pytest.raises(LedgerError, match="URL must not contain control characters"):
            close_bounty(
                session,
                bounty_id=bounty.id,
                closed_by="maintainer",
                reference="https://github.com/ramimbo/mergework/issues/13\x7f",
            )


def test_public_url_or_none_omits_control_character_urls() -> None:
    assert public_url_or_none("https://github.com/ramimbo/mergework/issues/14\nextra") is None
    assert public_url_or_none("\nhttps://github.com/ramimbo/mergework/issues/14") is None
    assert public_url_or_none("https://github.com/ramimbo/mergework/issues/14\n") is None
    assert public_url_or_none("https://127.0.0.1/ramimbo/mergework/issues/14") is None
    assert public_url_or_none("https://100.64.0.1/ramimbo/mergework/issues/14") is None
    assert public_url_or_none("https://224.0.0.1/ramimbo/mergework/issues/14") is None
    assert (
        public_url_or_none("https://8.8.8.8/ramimbo/mergework/issues/14")
        == "https://8.8.8.8/ramimbo/mergework/issues/14"
    )
    assert (
        public_url_or_none(" https://github.com/ramimbo/mergework/issues/14 ")
        == "https://github.com/ramimbo/mergework/issues/14"
    )


def test_bounty_fields_reject_oversized_values(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        with pytest.raises(LedgerError, match="title is too long"):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=7,
                issue_url="https://github.com/ramimbo/mergework/issues/7",
                title="x" * 301,
                reward_mrwk="1",
                acceptance="Maintainer applies mrwk:accepted",
            )


def test_bounty_text_fields_reject_control_characters(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        for field, value in (
            ("repo", "ramimbo/mergework\nmalformed"),
            ("title", "Control\tTitle"),
            ("acceptance", "Maintainer applies mrwk:accepted\x7f"),
        ):
            payload = {
                "repo": "ramimbo/mergework",
                "issue_number": 7,
                "issue_url": "https://github.com/ramimbo/mergework/issues/7",
                "title": "Control character hardening",
                "reward_mrwk": "1",
                "acceptance": "Maintainer applies mrwk:accepted",
            }
            payload[field] = value
            with pytest.raises(LedgerError, match=f"{field} must not contain control characters"):
                create_bounty(session, **payload)


def test_ledger_reference_unsafe_urls_are_not_rendered_as_links(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        add_ledger_entry(
            session,
            entry_type="security_test",
            from_account=TREASURY_ACCOUNT,
            to_account="github:alice",
            amount_microunits=1,
            reference="javascript:alert(1)",
        )
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    page = client.get("/ledger/2").text

    assert "javascript:alert(1)" in page
    assert 'href="javascript:alert(1)"' not in page


def test_signed_webhook_with_invalid_json_is_rejected(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = b"not-json"
    headers = {
        "X-GitHub-Delivery": "delivery-invalid-json",
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": _signature("secret", body),
    }

    result = handle_github_webhook(sqlite_url, headers, body, "secret")

    assert result == {"status": "invalid_payload"}
    with session_scope(sqlite_url) as session:
        event = session.get(WebhookEvent, "delivery-invalid-json")
        assert event is not None
        assert event.processed_status == "invalid_payload"


def test_duplicate_webhook_delivery_with_different_payload_is_rejected(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    first_body = json.dumps({"action": "opened"}, separators=(",", ":")).encode()
    second_body = json.dumps({"action": "labeled"}, separators=(",", ":")).encode()
    headers = {
        "X-GitHub-Delivery": "delivery-conflict",
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": _signature("secret", first_body),
    }

    first = handle_github_webhook(sqlite_url, headers, first_body, "secret")
    headers["X-Hub-Signature-256"] = _signature("secret", second_body)
    second = handle_github_webhook(sqlite_url, headers, second_body, "secret")

    assert first["status"] == "ignored"
    assert second == {"status": "delivery_payload_mismatch"}


def test_webhook_rejects_unapproved_accepted_labeler(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "issue": {
                "number": 42,
                "html_url": "https://github.com/ramimbo/mergework/issues/42",
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "not-maintainer"},
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "X-GitHub-Delivery": "delivery-unapproved-labeler",
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": _signature("secret", body),
    }
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=42,
            issue_url="https://github.com/ramimbo/mergework/issues/42",
            title="Accepted issue",
            reward_mrwk="200",
            acceptance="Maintainer applies mrwk:accepted",
        )

    result = handle_github_webhook(
        sqlite_url, headers, body, "secret", accepted_labelers=("maintainer",)
    )

    assert result == {"status": "unauthorized_labeler"}


def test_mcp_malformed_tool_call_returns_jsonrpc_error(sqlite_url: str) -> None:
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_bounty", "arguments": {}},
        },
    )

    assert response.status_code == 200
    assert response.json()["error"] == {"code": -32602, "message": "invalid tool arguments"}


def test_pay_bounty_rejects_reentrant_duplicate_before_ledger_write(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=9,
            issue_url="https://github.com/ramimbo/mergework/issues/9",
            title="Race guard",
            reward_mrwk="5",
            acceptance="Maintainer applies mrwk:accepted",
        )
        bounty_id = bounty.id

        original_add = ledger_service.add_ledger_entry
        reentered = False

        def racing_add_ledger_entry(*args: object, **kwargs: object) -> LedgerEntry:
            nonlocal reentered
            if kwargs.get("entry_type") == "bounty_payment" and not reentered:
                reentered = True
                with pytest.raises(LedgerError, match="already paid"):
                    ledger_service.pay_bounty(
                        session,
                        bounty_id=bounty_id,
                        to_account="github:eve",
                        submission_url="https://github.com/ramimbo/mergework/pull/99",
                        accepted_by="maintainer",
                        verifier_result={"label": "mrwk:accepted", "attempt": "race"},
                    )
            return original_add(*args, **kwargs)

        monkeypatch.setattr(ledger_service, "add_ledger_entry", racing_add_ledger_entry)

        pay_bounty(
            session,
            bounty_id=bounty_id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/10",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        payments = session.scalars(
            select(LedgerEntry).where(LedgerEntry.entry_type == "bounty_payment")
        ).all()
        assert reentered is True
        assert len(payments) == 1
