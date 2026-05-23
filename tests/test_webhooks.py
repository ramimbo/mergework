from __future__ import annotations

import hashlib
import hmac
import json

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, get_balance, register_wallet
from app.models import WebhookEvent
from app.webhooks.github import handle_github_webhook, verify_github_signature


def _signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_github_signature_verification() -> None:
    body = b'{"ok":true}'
    assert verify_github_signature(body, _signature("secret", body), "secret") is True
    assert verify_github_signature(body, "sha256=bad", "secret") is False


def test_accepted_label_pays_bounty_once(sqlite_url: str) -> None:
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
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "X-GitHub-Delivery": "delivery-1",
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

    first = handle_github_webhook(sqlite_url, headers, body, "secret")
    second = handle_github_webhook(sqlite_url, headers, body, "secret")

    assert first["status"] == "paid"
    assert second["status"] == "duplicate"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:alice") == 200_000_000
        assert session.query(WebhookEvent).count() == 1


def test_accepted_label_pays_linked_wallet(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "issue": {
                "number": 43,
                "html_url": "https://github.com/ramimbo/mergework/issues/43",
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "X-GitHub-Delivery": "delivery-3",
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": _signature("secret", body),
    }
    public_key_hex = "11" * 32

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        wallet = register_wallet(
            session, public_key_hex=public_key_hex, label="Alice", github_login="alice"
        )
        wallet_address = wallet.address
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=43,
            issue_url="https://github.com/ramimbo/mergework/issues/43",
            title="Accepted linked issue",
            reward_mrwk="200",
            acceptance="Maintainer applies mrwk:accepted",
        )

    result = handle_github_webhook(sqlite_url, headers, body, "secret")

    assert result["status"] == "paid"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, wallet_address) == 200_000_000
        assert get_balance(session, "github:alice") == 0


def test_accepted_pr_label_pays_pr_author_for_linked_bounty_issue(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "pull_request": {
                "number": 8,
                "html_url": "https://github.com/ramimbo/mergework/pull/8",
                "body": "Closes #3",
                "user": {"login": "contributor"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "X-GitHub-Delivery": "delivery-pr-accepted",
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": _signature("secret", body),
    }

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=3,
            issue_url="https://github.com/ramimbo/mergework/issues/3",
            title="Wallet transfer validation tests",
            reward_mrwk="150",
            acceptance="PR adds focused wallet transfer failure tests.",
        )

    result = handle_github_webhook(sqlite_url, headers, body, "secret")

    assert result["status"] == "paid"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:contributor") == 150_000_000
        assert get_balance(session, "github:maintainer") == 0


def test_accepted_maintainer_issue_label_requires_manual_payout(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "issue": {
                "number": 2,
                "html_url": "https://github.com/ramimbo/mergework/issues/2",
                "user": {"login": "maintainer"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "X-GitHub-Delivery": "delivery-manual-required",
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": _signature("secret", body),
    }

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=2,
            issue_url="https://github.com/ramimbo/mergework/issues/2",
            title="Star repo and verify wallet claim flow",
            reward_mrwk="25",
            acceptance="Contributor comments with wallet-flow proof.",
        )

    result = handle_github_webhook(
        sqlite_url, headers, body, "secret", accepted_labelers=("maintainer",)
    )

    assert result == {"status": "manual_payout_required"}
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:maintainer") == 0
        event = session.get(WebhookEvent, "delivery-manual-required")
        assert event is not None
        assert event.processed_status == "manual_payout_required"


def test_webhook_rejects_bad_signature(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = b'{"action":"labeled"}'
    headers = {
        "X-GitHub-Delivery": "delivery-2",
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": "sha256=bad",
    }

    result = handle_github_webhook(sqlite_url, headers, body, "secret")

    assert result["status"] == "unauthorized"
