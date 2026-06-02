from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.bounty_attempts import bounty_attempt_to_dict, bounty_attempt_warnings
from app.db import create_schema, session_scope
from app.ledger.service import close_bounty, create_bounty, ensure_genesis, pay_bounty
from app.main import _signed_value, create_app
from app.models import BountyAttempt, LedgerEntry
from app.treasury import propose_treasury_action

COOKIE_SECRET = "test-cookie-secret"


def _set_login(client: TestClient, login: str) -> None:
    client.cookies.set("mrwk_user", _signed_value(login, COOKIE_SECRET))


def test_bounty_attempt_serializer_reports_expired_effective_status() -> None:
    now = datetime(2026, 5, 25, 12, 0)
    stored_now = now.replace(tzinfo=UTC)
    attempt = BountyAttempt(
        id=7,
        bounty_id=321,
        submitter_account="github:alice",
        source_url="https://github.com/ramimbo/mergework/pull/500",
        status="active",
        expires_at=stored_now - timedelta(minutes=1),
        created_at=stored_now - timedelta(hours=2),
        updated_at=stored_now - timedelta(hours=2),
    )

    payload = bounty_attempt_to_dict(attempt, now)

    assert payload["status"] == "expired"
    assert payload["expires_at"] == "2026-05-25T11:59:00+00:00"


def test_bounty_attempts_register_list_duplicate_and_release(sqlite_url: str, monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", COOKIE_SECRET)
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Attempt reservations",
            reward_mrwk="250",
            max_awards=2,
            acceptance="Register active attempts before opening overlapping PRs.",
        )
        ledger_height = session.scalar(
            select(LedgerEntry.sequence).order_by(LedgerEntry.sequence.desc())
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _set_login(client, "alice")

    created = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={
            "submitter_account": "GitHub:Alice",
            "source_url": "https://github.com/ramimbo/mergework/pull/500",
            "ttl_seconds": 3600,
        },
    )

    assert created.status_code == 201
    first_attempt = created.json()["attempt"]
    assert first_attempt["submitter_account"] == "github:alice"
    assert first_attempt["source_url"] == "https://github.com/ramimbo/mergework/pull/500"
    assert first_attempt["status"] == "active"
    assert created.json()["warnings"] == []

    duplicate = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={"submitter_account": "github:alice", "ttl_seconds": 3600},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["status"] == "duplicate_active_attempt"
    assert duplicate.json()["attempt"]["id"] == first_attempt["id"]

    spoofed = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={"submitter_account": "github:bob", "ttl_seconds": 3600},
    )
    assert spoofed.status_code == 403

    _set_login(client, "bob")
    second = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={"submitter_account": "github:bob", "ttl_seconds": 3600},
    )
    assert second.status_code == 201
    assert second.json()["warnings"] == ["bounty has 2 active attempts"]

    visible = client.get(f"/api/v1/bounties/{bounty.id}/attempts")
    assert visible.status_code == 200
    body = visible.json()
    assert body["warnings"] == ["bounty has 2 active attempts"]
    assert [attempt["submitter_account"] for attempt in body["attempts"]] == [
        "github:bob",
        "github:alice",
    ]
    for noncanonical_id in (f"{bounty.id}.0", f"+{bounty.id}", f"%C2%85{bounty.id}"):
        response = client.get(f"/api/v1/bounties/{noncanonical_id}/attempts")
        assert response.status_code == 400
        assert response.json()["detail"] == "bounty id must be a positive integer"

    limited = client.get(f"/api/v1/bounties/{bounty.id}/attempts?limit=1")
    assert limited.status_code == 200
    assert [attempt["submitter_account"] for attempt in limited.json()["attempts"]] == [
        "github:bob"
    ]

    assert client.get(f"/api/v1/bounties/{bounty.id}/attempts?limit=0").status_code == 422
    assert client.get(f"/api/v1/bounties/{bounty.id}/attempts?limit=101").status_code == 422
    noncanonical_limits = {
        "%C2%851": "limit must not contain control characters",
        "1.0": "limit must be a canonical positive integer",
        "%2B1": "limit must be a canonical positive integer",
        "01": "limit must be a canonical positive integer",
    }
    for query, expected_detail in noncanonical_limits.items():
        response = client.get(f"/api/v1/bounties/{bounty.id}/attempts?limit={query}")
        assert response.status_code == 400
        assert response.json()["detail"] == expected_detail

    repeated_limit = client.get(f"/api/v1/bounties/{bounty.id}/attempts?limit=bad&limit=1")
    assert repeated_limit.status_code == 400
    assert repeated_limit.json()["detail"] == "limit must be provided at most once"

    repeated_include_expired = client.get(
        f"/api/v1/bounties/{bounty.id}/attempts?include_expired=bad&include_expired=false"
    )
    assert repeated_include_expired.status_code == 400
    assert (
        repeated_include_expired.json()["detail"] == "include_expired must be provided at most once"
    )

    wrong_submitter = client.post(
        f"/api/v1/bounty-attempts/{first_attempt['id']}/release",
        json={"submitter_account": "github:bob"},
    )
    assert wrong_submitter.status_code == 403

    _set_login(client, "alice")
    released = client.post(
        f"/api/v1/bounty-attempts/{first_attempt['id']}/release",
        json={"submitter_account": "github:alice"},
    )
    assert released.status_code == 200
    assert released.json()["status"] == "released"
    assert released.json()["attempt"]["status"] == "released"

    active_after_release = client.get(f"/api/v1/bounties/{bounty.id}/attempts").json()
    assert [attempt["submitter_account"] for attempt in active_after_release["attempts"]] == [
        "github:bob"
    ]

    all_attempts = client.get(f"/api/v1/bounties/{bounty.id}/attempts?include_expired=true").json()
    assert [attempt["status"] for attempt in all_attempts["attempts"]] == [
        "active",
        "released",
    ]

    with session_scope(sqlite_url) as session:
        assert (
            session.scalar(select(LedgerEntry.sequence).order_by(LedgerEntry.sequence.desc()))
            == ledger_height
        )


def test_bounty_attempts_accept_empty_body_defaults_to_login(sqlite_url: str, monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", COOKIE_SECRET)
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=325,
            issue_url="https://github.com/ramimbo/mergework/issues/325",
            title="Bodyless attempt registration",
            reward_mrwk="250",
            acceptance="Default attempt account from the authenticated GitHub login.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _set_login(client, "carol")

    created = client.post(f"/api/v1/bounties/{bounty.id}/attempts")

    assert created.status_code == 201
    attempt = created.json()["attempt"]
    assert attempt["submitter_account"] == "github:carol"
    assert attempt["source_url"] is None

    released = client.post(f"/api/v1/bounty-attempts/{attempt['id']}/release")

    assert released.status_code == 200
    assert released.json()["status"] == "released"
    assert released.json()["attempt"]["status"] == "released"


def test_bounty_attempt_source_url_rejects_raw_control_characters(
    sqlite_url: str, monkeypatch
) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", COOKIE_SECRET)
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=328,
            issue_url="https://github.com/ramimbo/mergework/issues/328",
            title="Attempt source URL validation",
            reward_mrwk="50",
            max_awards=1,
            acceptance="Attempt source URLs should be validated before normalization.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _set_login(client, "alice")

    response = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={
            "source_url": "\thttps://github.com/ramimbo/mergework/pull/616",
            "ttl_seconds": 3600,
        },
    )

    assert response.status_code == 400
    assert "control character" in response.json()["detail"].lower()
    with session_scope(sqlite_url) as session:
        assert session.scalars(select(BountyAttempt)).all() == []


def test_single_award_bounty_warns_when_active_attempt_uses_available_slot(
    sqlite_url: str, monkeypatch
) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", COOKIE_SECRET)
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=326,
            issue_url="https://github.com/ramimbo/mergework/issues/326",
            title="Single award attempt warning",
            reward_mrwk="50",
            max_awards=1,
            acceptance="Warn when the only award slot already has an active attempt.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _set_login(client, "alice")

    created = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={"submitter_account": "github:alice", "ttl_seconds": 3600},
    )

    assert created.status_code == 201
    assert created.json()["warnings"] == ["bounty has 1 active attempt"]

    visible = client.get(f"/api/v1/bounties/{bounty.id}/attempts")
    assert visible.status_code == 200
    assert visible.json()["warnings"] == ["bounty has 1 active attempt"]


def test_multi_award_bounty_still_warns_for_multiple_active_attempts(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    now = datetime.now(UTC)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=327,
            issue_url="https://github.com/ramimbo/mergework/issues/327",
            title="Multi award attempt warning",
            reward_mrwk="50",
            max_awards=3,
            acceptance="Warn when multiple contributors have active attempts.",
        )
        for submitter in ("github:alice", "github:bob"):
            session.add(
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account=submitter,
                    status="active",
                    expires_at=now + timedelta(hours=1),
                    created_at=now,
                    updated_at=now,
                )
            )
        session.flush()

        assert bounty_attempt_warnings(session, bounty, now) == ["bounty has 2 active attempts"]


def test_bounty_attempt_warnings_respect_full_pending_payout_capacity(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    now = datetime.now(UTC)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=329,
            issue_url="https://github.com/ramimbo/mergework/issues/329",
            title="Full pending payout attempt warning",
            reward_mrwk="50",
            max_awards=2,
            acceptance="Effective capacity should block attempts.",
        )
        for pull_number, submitter in ((329, "github:alice"), (330, "github:bob")):
            propose_treasury_action(
                session,
                action="pay_bounty",
                payload={
                    "bounty_id": bounty.id,
                    "to_account": submitter,
                    "submission_url": f"https://github.com/ramimbo/mergework/pull/{pull_number}",
                    "accepted_by": "maintainer",
                },
                proposed_by="maintainer",
            )

        assert bounty_attempt_warnings(session, bounty, now) == [
            "bounty has no award slots remaining",
            "2 awards covered by pending payout proposals; 0 awards effectively available.",
        ]


def test_bounty_attempt_registration_blocks_full_pending_payout_capacity(
    sqlite_url: str, monkeypatch
) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", COOKIE_SECRET)
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=330,
            issue_url="https://github.com/ramimbo/mergework/issues/330",
            title="Full pending payout attempt creation",
            reward_mrwk="50",
            acceptance="Attempt creation should use effective capacity.",
        )
        propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": bounty.id,
                "to_account": "github:winner",
                "submission_url": "https://github.com/ramimbo/mergework/pull/330",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _set_login(client, "alice")

    response = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={"submitter_account": "github:alice", "ttl_seconds": 3600},
    )

    assert response.status_code == 409
    assert response.json() == {
        "status": "not_available",
        "bounty_id": bounty.id,
        "warnings": [
            "bounty has no award slots remaining",
            "1 award covered by pending payout proposal; 0 awards effectively available.",
        ],
    }


def test_bounty_attempt_registration_allows_partial_pending_payout_capacity(
    sqlite_url: str, monkeypatch
) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", COOKIE_SECRET)
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=331,
            issue_url="https://github.com/ramimbo/mergework/issues/331",
            title="Partial pending payout attempt creation",
            reward_mrwk="50",
            max_awards=2,
            acceptance="Positive effective capacity should remain startable.",
        )
        propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": bounty.id,
                "to_account": "github:winner",
                "submission_url": "https://github.com/ramimbo/mergework/pull/331",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _set_login(client, "alice")

    response = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={"submitter_account": "github:alice", "ttl_seconds": 3600},
    )

    assert response.status_code == 201
    assert response.json()["warnings"] == [
        "1 award covered by pending payout proposal; 1 award effectively available.",
        "bounty has 1 active attempt",
    ]


def test_expired_bounty_attempt_is_visible_but_no_longer_blocks_submitter(
    sqlite_url: str,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", COOKIE_SECRET)
    create_schema(sqlite_url)
    now = datetime.now(UTC)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=322,
            issue_url="https://github.com/ramimbo/mergework/issues/322",
            title="Expired attempt reservation",
            reward_mrwk="250",
            acceptance="Expired attempts should not block future contributors.",
        )
        session.add(
            BountyAttempt(
                bounty_id=bounty.id,
                submitter_account="github:alice",
                source_url="https://github.com/ramimbo/mergework/pull/501",
                status="active",
                expires_at=now - timedelta(minutes=5),
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
            )
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _set_login(client, "alice")

    active_only = client.get(f"/api/v1/bounties/{bounty.id}/attempts").json()
    assert active_only["attempts"] == []

    with_expired = client.get(f"/api/v1/bounties/{bounty.id}/attempts?include_expired=true").json()
    assert with_expired["attempts"][0]["status"] == "expired"

    replacement = client.post(
        f"/api/v1/bounties/{bounty.id}/attempts",
        json={"submitter_account": "github:alice", "ttl_seconds": 3600},
    )
    assert replacement.status_code == 201
    assert replacement.json()["attempt"]["status"] == "active"


def test_attempt_registration_rejects_closed_and_exhausted_bounties(
    sqlite_url: str, monkeypatch
) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", COOKIE_SECRET)
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        closed = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=323,
            issue_url="https://github.com/ramimbo/mergework/issues/323",
            title="Closed bounty",
            reward_mrwk="100",
            acceptance="Closed bounties should not accept new attempts.",
        )
        close_bounty(session, bounty_id=closed.id, closed_by="maintainer")
        paid = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=324,
            issue_url="https://github.com/ramimbo/mergework/issues/324",
            title="Paid bounty",
            reward_mrwk="100",
            acceptance="Paid bounties should not accept new attempts.",
        )
        pay_bounty(
            session,
            bounty_id=paid.id,
            to_account="github:winner",
            submission_url="https://github.com/ramimbo/mergework/pull/324",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _set_login(client, "alice")

    closed_response = client.post(
        f"/api/v1/bounties/{closed.id}/attempts",
        json={"submitter_account": "github:alice", "ttl_seconds": 3600},
    )
    assert closed_response.status_code == 409
    assert closed_response.json()["status"] == "not_available"
    assert closed_response.json()["warnings"] == [
        "bounty is closed",
        "bounty has no award slots remaining",
    ]

    paid_response = client.post(
        f"/api/v1/bounties/{paid.id}/attempts",
        json={"submitter_account": "github:alice", "ttl_seconds": 3600},
    )
    assert paid_response.status_code == 409
    assert paid_response.json()["status"] == "not_available"
    assert paid_response.json()["warnings"] == [
        "bounty is paid",
        "bounty has no award slots remaining",
    ]
