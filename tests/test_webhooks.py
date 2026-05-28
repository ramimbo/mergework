from __future__ import annotations

import hashlib
import hmac
import json

from app.db import create_schema, session_scope
from app.ledger.service import (
    close_bounty,
    create_bounty,
    ensure_genesis,
    get_balance,
    register_wallet,
)
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


def test_accepted_pr_label_accepts_claim_bounty_reference(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "pull_request": {
                "number": 9,
                "html_url": "https://github.com/ramimbo/mergework/pull/9",
                "body": "Summary: focused fix\n\n/claim #3\n\nTests: pytest passed",
                "user": {"login": "contributor"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=3,
            issue_url="https://github.com/ramimbo/mergework/issues/3",
            title="Claim reference payout",
            reward_mrwk="50",
            acceptance="Accepted PRs can cite the bounty with /claim.",
        )

    result = handle_github_webhook(
        sqlite_url,
        {
            "X-GitHub-Delivery": "delivery-pr-claim-reference",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _signature("secret", body),
        },
        body,
        "secret",
    )

    assert result["status"] == "paid"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:contributor") == 50_000_000
        event = session.get(WebhookEvent, "delivery-pr-claim-reference")
        assert event is not None
        assert event.processed_status == "paid"


def test_accepted_pr_label_pays_pr_author_for_colon_bounty_reference(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "pull_request": {
                "number": 9,
                "html_url": "https://github.com/ramimbo/mergework/pull/9",
                "body": "Bounty: #3",
                "user": {"login": "contributor"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "X-GitHub-Delivery": "delivery-pr-colon-bounty-ref",
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
    replay = handle_github_webhook(sqlite_url, headers, body, "secret")

    assert result["status"] == "paid"
    assert replay == {"status": "duplicate", "processed_status": "paid"}
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:contributor") == 150_000_000
        assert get_balance(session, "github:maintainer") == 0
        event = session.get(WebhookEvent, "delivery-pr-colon-bounty-ref")
        assert event is not None
        assert event.processed_status == "paid"


def test_accepted_issue_event_for_pull_request_does_not_pay_matching_bounty(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "issue": {
                "number": 3,
                "html_url": "https://github.com/ramimbo/mergework/pull/3",
                "pull_request": {
                    "html_url": "https://github.com/ramimbo/mergework/pull/3",
                    "url": "https://api.github.com/repos/ramimbo/mergework/pulls/3",
                },
                "user": {"login": "contributor"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "X-GitHub-Delivery": "delivery-pr-issue-event",
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": _signature("secret", body),
    }

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=3,
            issue_url="https://github.com/ramimbo/mergework/issues/3",
            title="Real bounty issue with matching PR number",
            reward_mrwk="150",
            acceptance="Maintainer applies mrwk:accepted to the accepted work.",
        )

    result = handle_github_webhook(
        sqlite_url, headers, body, "secret", accepted_labelers=("maintainer",)
    )

    assert result == {"status": "ignored_pull_request_issue"}
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:contributor") == 0
        event = session.get(WebhookEvent, "delivery-pr-issue-event")
        assert event is not None
        assert event.processed_status == "ignored_pull_request_issue"


def test_accepted_pr_label_rejects_malformed_body_objects(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "pull_request": {
                "number": 8,
                "html_url": "https://github.com/ramimbo/mergework/pull/8",
                "body": {"text": "Bounty #3"},
                "user": {"login": "contributor"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "X-GitHub-Delivery": "delivery-pr-malformed-body",
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

    assert result == {"status": "malformed_pull_request_body"}
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:contributor") == 0
        event = session.get(WebhookEvent, "delivery-pr-malformed-body")
        assert event is not None
        assert event.processed_status == "malformed_pull_request_body"


def test_accepted_pr_label_rejects_malformed_issue_reference_suffixes(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    cases = [
        ("delivery-pr-malformed-shorthand-issue-ref", "Closes #3abc"),
        ("delivery-pr-malformed-shorthand-underscore-ref", "Closes #3_abc"),
        ("delivery-pr-malformed-shorthand-hyphen-ref", "Closes #3-abc"),
        (
            "delivery-pr-malformed-url-issue-ref",
            "Implements https://github.com/ramimbo/mergework/issues/3abc",
        ),
        (
            "delivery-pr-malformed-url-hyphen-ref",
            "Implements https://github.com/ramimbo/mergework/issues/3-abc",
        ),
        (
            "delivery-pr-malformed-url-underscore-ref",
            "Implements https://github.com/ramimbo/mergework/issues/3_abc",
        ),
    ]

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

    for delivery_id, pr_body in cases:
        body = json.dumps(
            {
                "action": "labeled",
                "label": {"name": "mrwk:accepted"},
                "pull_request": {
                    "number": 8,
                    "html_url": "https://github.com/ramimbo/mergework/pull/8",
                    "body": pr_body,
                    "user": {"login": "contributor"},
                },
                "repository": {"full_name": "ramimbo/mergework"},
                "sender": {"login": "maintainer"},
            },
            separators=(",", ":"),
        ).encode()

        result = handle_github_webhook(
            sqlite_url,
            {
                "X-GitHub-Delivery": delivery_id,
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": _signature("secret", body),
            },
            body,
            "secret",
        )

        assert result == {"status": "missing_issue"}

    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:contributor") == 0
        for delivery_id, _pr_body in cases:
            event = session.get(WebhookEvent, delivery_id)
            assert event is not None
            assert event.processed_status == "missing_issue"


def test_accepted_label_requires_submitter_login(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "issue": {
                "number": 44,
                "html_url": "https://github.com/ramimbo/mergework/issues/44",
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "X-GitHub-Delivery": "delivery-missing-submitter",
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": _signature("secret", body),
    }

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=44,
            issue_url="https://github.com/ramimbo/mergework/issues/44",
            title="Accepted issue",
            reward_mrwk="25",
            acceptance="Maintainer applies mrwk:accepted",
        )

    result = handle_github_webhook(sqlite_url, headers, body, "secret")

    assert result == {"status": "missing_submitter"}
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:unknown") == 0
        event = session.get(WebhookEvent, "delivery-missing-submitter")
        assert event is not None
        assert event.processed_status == "missing_submitter"


def test_accepted_issue_label_rejects_boolean_issue_number(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "issue": {
                "number": True,
                "html_url": "https://github.com/ramimbo/mergework/issues/1",
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "X-GitHub-Delivery": "delivery-boolean-issue-number",
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": _signature("secret", body),
    }

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=1,
            issue_url="https://github.com/ramimbo/mergework/issues/1",
            title="Real issue one bounty",
            reward_mrwk="25",
            acceptance="Boolean issue numbers must not match this bounty.",
        )

    result = handle_github_webhook(
        sqlite_url, headers, body, "secret", accepted_labelers=("maintainer",)
    )

    assert result == {"status": "missing_issue"}
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:alice") == 0
        event = session.get(WebhookEvent, "delivery-boolean-issue-number")
        assert event is not None
        assert event.processed_status == "missing_issue"


def test_accepted_labels_ignore_oversized_issue_numbers(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    oversized_issue = "9" * 30
    cases = [
        (
            "delivery-oversized-issue-number",
            "issues",
            {
                "action": "labeled",
                "label": {"name": "mrwk:accepted"},
                "issue": {
                    "number": int(oversized_issue),
                    "html_url": f"https://github.com/ramimbo/mergework/issues/{oversized_issue}",
                    "user": {"login": "alice"},
                },
                "repository": {"full_name": "ramimbo/mergework"},
                "sender": {"login": "maintainer"},
            },
        ),
        (
            "delivery-oversized-pr-issue-reference",
            "pull_request",
            {
                "action": "labeled",
                "label": {"name": "mrwk:accepted"},
                "pull_request": {
                    "number": 8,
                    "html_url": "https://github.com/ramimbo/mergework/pull/8",
                    "body": f"Closes #{oversized_issue}",
                    "user": {"login": "alice"},
                },
                "repository": {"full_name": "ramimbo/mergework"},
                "sender": {"login": "maintainer"},
            },
        ),
    ]

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=3,
            issue_url="https://github.com/ramimbo/mergework/issues/3",
            title="Valid issue bounty",
            reward_mrwk="25",
            acceptance="Oversized issue numbers must not match or overflow lookup.",
        )

    for delivery_id, event_type, payload in cases:
        body = json.dumps(payload, separators=(",", ":")).encode()
        result = handle_github_webhook(
            sqlite_url,
            {
                "X-GitHub-Delivery": delivery_id,
                "X-GitHub-Event": event_type,
                "X-Hub-Signature-256": _signature("secret", body),
            },
            body,
            "secret",
        )

        assert result == {"status": "missing_issue"}

    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:alice") == 0
        for delivery_id, _event_type, _payload in cases:
            event = session.get(WebhookEvent, delivery_id)
            assert event is not None
            assert event.processed_status == "missing_issue"


def test_accepted_label_requires_sender_login(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "issue": {
                "number": 45,
                "html_url": "https://github.com/ramimbo/mergework/issues/45",
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": 123},
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "X-GitHub-Delivery": "delivery-missing-sender",
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": _signature("secret", body),
    }

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=45,
            issue_url="https://github.com/ramimbo/mergework/issues/45",
            title="Accepted issue with malformed sender",
            reward_mrwk="25",
            acceptance="Maintainer applies mrwk:accepted",
        )

    result = handle_github_webhook(sqlite_url, headers, body, "secret")

    assert result == {"status": "missing_sender"}
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:alice") == 0
        event = session.get(WebhookEvent, "delivery-missing-sender")
        assert event is not None
        assert event.processed_status == "missing_sender"


def test_accepted_label_handles_malformed_object_fields(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    cases = [
        (
            "delivery-malformed-label",
            {
                "action": "labeled",
                "label": "mrwk:accepted",
                "issue": {
                    "number": 44,
                    "html_url": "https://github.com/ramimbo/mergework/issues/44",
                    "user": {"login": "alice"},
                },
                "repository": {"full_name": "ramimbo/mergework"},
                "sender": {"login": "maintainer"},
            },
            "ignored",
        ),
        (
            "delivery-malformed-repo",
            {
                "action": "labeled",
                "label": {"name": "mrwk:accepted"},
                "issue": {
                    "number": 44,
                    "html_url": "https://github.com/ramimbo/mergework/issues/44",
                    "user": {"login": "alice"},
                },
                "repository": "ramimbo/mergework",
                "sender": {"login": "maintainer"},
            },
            "missing_issue",
        ),
        (
            "delivery-malformed-issue",
            {
                "action": "labeled",
                "label": {"name": "mrwk:accepted"},
                "issue": "ramimbo/mergework#44",
                "repository": {"full_name": "ramimbo/mergework"},
                "sender": {"login": "maintainer"},
            },
            "missing_issue",
        ),
        (
            "delivery-malformed-submission-url",
            {
                "action": "labeled",
                "label": {"name": "mrwk:accepted"},
                "issue": {
                    "number": 44,
                    "html_url": {"href": "https://github.com/ramimbo/mergework/issues/44"},
                    "user": {"login": "alice"},
                },
                "repository": {"full_name": "ramimbo/mergework"},
                "sender": {"login": "maintainer"},
            },
            "malformed_submission_url",
        ),
    ]

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=44,
            issue_url="https://github.com/ramimbo/mergework/issues/44",
            title="Accepted issue",
            reward_mrwk="25",
            acceptance="Maintainer applies mrwk:accepted",
        )

    for delivery_id, payload, expected_status in cases:
        body = json.dumps(payload, separators=(",", ":")).encode()
        result = handle_github_webhook(
            sqlite_url,
            {
                "X-GitHub-Delivery": delivery_id,
                "X-GitHub-Event": "issues",
                "X-Hub-Signature-256": _signature("secret", body),
            },
            body,
            "secret",
        )

        assert result == {"status": expected_status}

    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:alice") == 0
        for delivery_id, _payload, expected_status in cases:
            event = session.get(WebhookEvent, delivery_id)
            assert event is not None
            assert event.processed_status == expected_status


def test_accepted_pr_labels_can_pay_multiple_awards_for_one_bounty(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    first_body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "pull_request": {
                "number": 8,
                "html_url": "https://github.com/ramimbo/mergework/pull/8",
                "body": "Closes #3",
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()
    second_body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "pull_request": {
                "number": 9,
                "html_url": "https://github.com/ramimbo/mergework/pull/9",
                "body": "Closes #3",
                "user": {"login": "bob"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=3,
            issue_url="https://github.com/ramimbo/mergework/issues/3",
            title="Multiple review awards",
            reward_mrwk="25",
            max_awards=2,
            acceptance="Each accepted PR can earn one award.",
        )

    first = handle_github_webhook(
        sqlite_url,
        {
            "X-GitHub-Delivery": "delivery-multi-1",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _signature("secret", first_body),
        },
        first_body,
        "secret",
    )
    second = handle_github_webhook(
        sqlite_url,
        {
            "X-GitHub-Delivery": "delivery-multi-2",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _signature("secret", second_body),
        },
        second_body,
        "secret",
    )

    assert first["status"] == "paid"
    assert second["status"] == "paid"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:alice") == 25_000_000
        assert get_balance(session, "github:bob") == 25_000_000


def test_accepted_pr_label_can_pay_referenced_multi_award_issue(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "pull_request": {
                "number": 10,
                "html_url": "https://github.com/ramimbo/mergework/pull/10",
                "body": "Bounty #3\n\nRefs #3",
                "user": {"login": "reviewer"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=3,
            issue_url="https://github.com/ramimbo/mergework/issues/3",
            title="Referenced multi-award issue",
            reward_mrwk="25",
            max_awards=2,
            acceptance="Each accepted PR can earn one award.",
        )

    result = handle_github_webhook(
        sqlite_url,
        {
            "X-GitHub-Delivery": "delivery-referenced-pr",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _signature("secret", body),
        },
        body,
        "secret",
    )

    assert result["status"] == "paid"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:reviewer") == 25_000_000


def test_accepted_pr_label_pays_repo_qualified_multi_award_issue(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "pull_request": {
                "number": 11,
                "html_url": "https://github.com/ramimbo/mergework/pull/11",
                "body": "Refs other/project#3\n\nBounty ramimbo/mergework#4",
                "user": {"login": "reviewer"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=4,
            issue_url="https://github.com/ramimbo/mergework/issues/4",
            title="Repo-qualified multi-award issue",
            reward_mrwk="25",
            max_awards=2,
            acceptance="Each accepted PR can earn one award.",
        )

    result = handle_github_webhook(
        sqlite_url,
        {
            "X-GitHub-Delivery": "delivery-repo-qualified-pr",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _signature("secret", body),
        },
        body,
        "secret",
    )

    assert result["status"] == "paid"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:reviewer") == 25_000_000


def test_accepted_pr_label_pays_full_github_issue_url_reference(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "pull_request": {
                "number": 12,
                "html_url": "https://github.com/ramimbo/mergework/pull/12",
                "body": "Implements the bounty at https://github.com/ramimbo/mergework/issues/5",
                "user": {"login": "reviewer"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=5,
            issue_url="https://github.com/ramimbo/mergework/issues/5",
            title="Full issue URL bounty reference",
            reward_mrwk="25",
            max_awards=2,
            acceptance="Accepted PR can link the full GitHub issue URL.",
        )

    result = handle_github_webhook(
        sqlite_url,
        {
            "X-GitHub-Delivery": "delivery-full-url-pr",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _signature("secret", body),
        },
        body,
        "secret",
    )

    assert result["status"] == "paid"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:reviewer") == 25_000_000


def test_accepted_pr_label_skips_closed_bounty_for_later_open_reference(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    body = json.dumps(
        {
            "action": "labeled",
            "label": {"name": "mrwk:accepted"},
            "pull_request": {
                "number": 13,
                "html_url": "https://github.com/ramimbo/mergework/pull/13",
                "body": "Fixes #3\n\nBounty #4",
                "user": {"login": "reviewer"},
            },
            "repository": {"full_name": "ramimbo/mergework"},
            "sender": {"login": "maintainer"},
        },
        separators=(",", ":"),
    ).encode()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        closed_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=3,
            issue_url="https://github.com/ramimbo/mergework/issues/3",
            title="Closed stale bounty",
            reward_mrwk="40",
            acceptance="Already closed bounty should not block later open refs.",
        )
        close_bounty(
            session,
            bounty_id=closed_bounty.id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/3#close",
        )
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=4,
            issue_url="https://github.com/ramimbo/mergework/issues/4",
            title="Current open bounty",
            reward_mrwk="25",
            acceptance="Accepted PR can mention stale refs before the bounty target.",
        )

    result = handle_github_webhook(
        sqlite_url,
        {
            "X-GitHub-Delivery": "delivery-closed-then-open-pr",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _signature("secret", body),
        },
        body,
        "secret",
    )

    assert result["status"] == "paid"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:reviewer") == 25_000_000
        event = session.get(WebhookEvent, "delivery-closed-then-open-pr")
        assert event is not None
        assert event.processed_status == "paid"


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
