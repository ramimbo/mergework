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
    close_bounty,
    create_bounty,
    ensure_genesis,
    get_balance,
    parse_mrwk_amount,
    pay_bounty,
    public_url_or_none,
    register_wallet,
)
from app.main import _signed_value, create_app
from app.models import LedgerEntry, Proof, WebhookEvent
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


def test_admin_bounty_api_rejects_duplicate_repo_issue(
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
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "bounty already exists for issue"


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

    assert fractional_issue.status_code == 400
    assert fractional_issue.json()["detail"] == "issue_number must be an integer"
    assert fractional_awards.status_code == 400
    assert fractional_awards.json()["detail"] == "max_awards must be an integer"


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
    assert token_auth.json()["status"] == "closed"
    assert token_auth.json()["released_mrwk"] == "50"


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
    assert body["reward_mrwk"] == "25"
    assert body["reserved_mrwk"] == "75"
    assert body["max_awards"] == 3
    assert body["awards_remaining"] == 3


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


def test_bounty_urls_reject_embedded_credentials(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        with pytest.raises(LedgerError, match="URL must not include credentials"):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=9,
                issue_url="https://operator:secret@github.com/ramimbo/mergework/issues/9",
                title="Credential-bearing URL",
                reward_mrwk="1",
                acceptance="Maintainer applies mrwk:accepted",
            )
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=10,
            issue_url="https://github.com/ramimbo/mergework/issues/10",
            title="Safe URL",
            reward_mrwk="1",
            acceptance="Maintainer applies mrwk:accepted",
        )
        with pytest.raises(LedgerError, match="URL must not include credentials"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:alice",
                submission_url="https://operator:secret@github.com/ramimbo/mergework/pull/10",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
            )


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


def test_admin_payout_api_ignores_blank_note(
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
            title="Admin proof metadata blank note",
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
    assert response.json()["status"] == "paid"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:alice") == 25_000_000
        proof = session.get(Proof, response.json()["proof_hash"])
        assert proof is not None
        verifier_result = json.loads(proof.public_json)["verifier_result"]
        assert "note" not in verifier_result


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
