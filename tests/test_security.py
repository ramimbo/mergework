from __future__ import annotations

import hashlib
import hmac
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.ledger.service as ledger_service
from app.db import create_schema, session_scope
from app.ledger.service import (
    TREASURY_ACCOUNT,
    LedgerError,
    add_ledger_entry,
    create_bounty,
    ensure_genesis,
    get_balance,
    parse_mrwk_amount,
    pay_bounty,
    register_wallet,
)
from app.main import _signed_value, create_app
from app.models import LedgerEntry, WebhookEvent
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
        "acceptance": "Maintainer applies mrwk:accepted",
    }
    if csrf_token is not None:
        data["csrf_token"] = csrf_token
    return data


def test_browser_responses_set_security_headers(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_admin_bounty_form_requires_csrf_for_cookie_auth(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    assert token_auth.json()["to_account"] == wallet_address
    with session_scope(sqlite_url) as session:
        assert get_balance(session, wallet_address) == 25_000_000


def test_amount_parser_rejects_non_finite_values() -> None:
    for amount in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(LedgerError, match="invalid MRWK amount"):
            parse_mrwk_amount(amount)


def test_amount_parser_rejects_values_above_fixed_supply() -> None:
    with pytest.raises(LedgerError, match="amount exceeds fixed supply"):
        parse_mrwk_amount("100000001")


def test_bounty_urls_reject_unsafe_schemes(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        with pytest.raises(LedgerError, match="URL must use http or https"):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=7,
                issue_url="javascript:alert(1)",
                title="Unsafe URL",
                reward_mrwk="1",
                acceptance="Maintainer applies mrwk:accepted",
            )
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=8,
            issue_url="https://github.com/ramimbo/mergework/issues/8",
            title="Safe URL",
            reward_mrwk="1",
            acceptance="Maintainer applies mrwk:accepted",
        )
        with pytest.raises(LedgerError, match="URL must use http or https"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:alice",
                submission_url="javascript:alert(1)",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
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


def test_ledger_and_proof_pages_surface_readable_metadata(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=6,
            issue_url="https://github.com/ramimbo/mergework/issues/6",
            title="Improve ledger explorer proof readability",
            reward_mrwk="100",
            acceptance="Maintainer applies mrwk:accepted",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/12",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    ledger_page = client.get("/ledger").text
    assert "Parties" in ledger_page
    assert "Reference" in ledger_page
    assert "github:alice" in ledger_page
    assert "/proofs/" in ledger_page

    entry_page = client.get(f"/ledger/{proof.ledger_sequence}").text
    assert "summary-grid" in entry_page
    assert "hash-block" in entry_page
    assert proof.hash in entry_page

    proof_page = client.get(f"/proofs/{proof.hash}").text
    assert "Public proof payload" in proof_page
    assert "Recipient" in proof_page
    assert "github:alice" in proof_page
    assert "100" in proof_page


def test_ledger_surfaces_bounty_release_entries(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=23,
            issue_url="https://github.com/ramimbo/mergework/issues/23",
            title="Scan bounty payments and releases",
            reward_mrwk="15",
            acceptance="Visible handling for bounty payment and release scanning.",
        )
        release_entry = add_ledger_entry(
            session,
            entry_type="bounty_release",
            from_account=f"reserve:bounty:{bounty.id}",
            to_account=TREASURY_ACCOUNT,
            amount_microunits=parse_mrwk_amount("15"),
            reference="https://github.com/ramimbo/mergework/issues/23#bounty-release",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    ledger_page = client.get("/ledger").text
    assert "bounty_release" in ledger_page
    assert "https://github.com/ramimbo/mergework/issues/23#bounty-release" in ledger_page
    assert f"/ledger/{release_entry.sequence}" in ledger_page

    entry_page = client.get(f"/ledger/{release_entry.sequence}").text
    assert "bounty_release" in entry_page
    assert "reserve:bounty:" in entry_page
    assert "treasury:mrwk" in entry_page


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
