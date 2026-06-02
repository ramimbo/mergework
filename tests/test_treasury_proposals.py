from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth import signed_value
from app.db import session_scope
from app.ledger.service import (
    TREASURY_ACCOUNT,
    canonical_json,
    create_bounty,
    ensure_genesis,
    get_balance,
    pay_bounty,
    register_wallet,
    verify_hash_chain,
    verify_supply_conservation,
)
from app.main import create_app
from app.models import Bounty, LedgerEntry, Submission, TreasuryChallenge, TreasuryProposal, utc_now
from app.path_params import SQLITE_INTEGER_MAX
from app.serializers import public_utc_timestamp

ADMIN_HEADERS = {"x-mergework-admin-token": "admin-token-for-tests"}


def _client(sqlite_url: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    return TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )


def _bounty_payload(issue_number: int = 77, reward_mrwk: str = "25") -> dict[str, object]:
    return {
        "repo": "ramimbo/mergework",
        "issue_number": issue_number,
        "issue_url": f"https://github.com/ramimbo/mergework/issues/{issue_number}",
        "title": f"Governance proposal test {issue_number}",
        "reward_mrwk": reward_mrwk,
        "max_awards": 1,
        "acceptance": "Maintainer applies mrwk:accepted.",
    }


def _make_executable(sqlite_url: str, proposal_id: int) -> None:
    with session_scope(sqlite_url) as session:
        proposal = session.get(TreasuryProposal, proposal_id)
        assert proposal is not None
        proposal.executes_after = utc_now() - timedelta(seconds=1)


def _stored_proposal_result(sqlite_url: str, proposal_id: int) -> dict[str, object]:
    with session_scope(sqlite_url) as session:
        stored = session.get(TreasuryProposal, proposal_id)
        assert stored is not None
        result = json.loads(stored.result_json)
        assert isinstance(result, dict)
        return cast(dict[str, object], result)


def _seed_accepted_work(session, github_login: str, issue_number: int = 20) -> None:
    earned_bounty = create_bounty(
        session,
        repo="ramimbo/mergework",
        issue_number=issue_number,
        issue_url=f"https://github.com/ramimbo/mergework/issues/{issue_number}",
        title="Earned work seed",
        reward_mrwk="1",
        acceptance="Accepted proof.",
    )
    pay_bounty(
        session,
        bounty_id=earned_bounty.id,
        to_account=f"github:{github_login}",
        submission_url=f"https://github.com/ramimbo/mergework/pull/{issue_number}",
        accepted_by="maintainer",
        verifier_result={"source": "test"},
    )


def test_admin_bounty_creation_creates_public_delayed_proposal(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)

    response = client.post("/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "treasury_proposal"
    assert body["action"] == "create_bounty"
    assert body["status"] == "pending"
    assert body["payload"]["repo"] == "ramimbo/mergework"
    assert body["payload"]["reward_mrwk"] == "25"
    assert body["executes_after"] > body["proposed_at"]
    assert body["proposed_at"].endswith("Z")
    assert body["executes_after"].endswith("Z")
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(Bounty.id))) == 0
        assert get_balance(session, TREASURY_ACCOUNT) == 100_000_000_000_000

    listed = client.get("/api/v1/treasury/proposals")
    detail = client.get(f"/api/v1/treasury/proposals/{body['id']}")

    assert listed.status_code == 200
    assert [proposal["id"] for proposal in listed.json()] == [body["id"]]
    assert detail.status_code == 200
    assert detail.json()["payload_hash"] == body["payload_hash"]
    for noncanonical_id in (f"{body['id']}.0", f"+{body['id']}", f"%C2%85{body['id']}"):
        noncanonical_detail = client.get(f"/api/v1/treasury/proposals/{noncanonical_id}")
        assert noncanonical_detail.status_code == 400
        assert noncanonical_detail.json()["detail"] == "proposal id must be a positive integer"


def test_admin_bounty_creation_rejects_control_character_reward_amount(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    payload = _bounty_payload(issue_number=79, reward_mrwk="\t25")

    response = client.post("/api/v1/bounties", headers=ADMIN_HEADERS, json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid MRWK amount"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 0


def test_treasury_proposals_list_newest_first(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    first = client.post(
        "/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload(issue_number=70)
    ).json()
    second = client.post(
        "/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload(issue_number=71)
    ).json()

    listed = client.get("/api/v1/treasury/proposals")

    assert listed.status_code == 200
    assert [proposal["id"] for proposal in listed.json()] == [second["id"], first["id"]]


def test_treasury_status_reports_reserve_capacity_and_pending_create_proposals(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    recent_time = utc_now() - timedelta(hours=2)
    old_time = utc_now() - timedelta(hours=25)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=1,
            issue_url="https://github.com/ramimbo/mergework/issues/1",
            title="Recent reserve",
            reward_mrwk="9000",
            acceptance="Accepted work.",
        )
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=2,
            issue_url="https://github.com/ramimbo/mergework/issues/2",
            title="Expired reserve",
            reward_mrwk="500",
            acceptance="Accepted work.",
        )
        recent_entry = session.scalar(
            select(LedgerEntry).where(
                LedgerEntry.entry_type == "bounty_reserve",
                LedgerEntry.reference == "https://github.com/ramimbo/mergework/issues/1",
            )
        )
        old_entry = session.scalar(
            select(LedgerEntry).where(
                LedgerEntry.entry_type == "bounty_reserve",
                LedgerEntry.reference == "https://github.com/ramimbo/mergework/issues/2",
            )
        )
        assert recent_entry is not None
        assert old_entry is not None
        recent_entry.created_at = recent_time
        old_entry.created_at = old_time

    proposal = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json={**_bounty_payload(issue_number=3, reward_mrwk="500"), "max_awards": 1},
    )
    response = client.get("/api/v1/treasury/status")

    assert proposal.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["reserve_cap_mrwk"] == "10000"
    assert body["executed_reserve_24h_mrwk"] == "9000"
    assert body["pending_create_reserve_mrwk"] == "500"
    assert body["available_create_reserve_mrwk"] == "500"
    assert body["next_capacity_release_at"] == public_utc_timestamp(
        recent_time + timedelta(hours=24)
    )
    assert body["pending_create_bounties"] == [
        {
            "proposal_id": proposal.json()["id"],
            "issue_number": 3,
            "issue_url": "https://github.com/ramimbo/mergework/issues/3",
            "title": "Governance proposal test 3",
            "reward_mrwk": "500",
            "max_awards": 1,
            "reserve_mrwk": "500",
            "proposed_at": proposal.json()["proposed_at"],
            "executes_after": proposal.json()["executes_after"],
            "capacity_releases_at": public_utc_timestamp(
                datetime.fromisoformat(proposal.json()["executes_after"]) + timedelta(hours=24)
            ),
        }
    ]
    assert body["recent_reserves"] == [
        {
            "ledger_sequence": recent_entry.sequence,
            "amount_mrwk": "9000",
            "reference": "https://github.com/ramimbo/mergework/issues/1",
            "created_at": public_utc_timestamp(recent_time),
            "expires_at": public_utc_timestamp(recent_time + timedelta(hours=24)),
        }
    ]


def test_treasury_status_includes_reserve_at_exact_24h_boundary(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    fixed_now = utc_now().replace(microsecond=0)
    boundary_time = fixed_now - timedelta(hours=24)
    monkeypatch.setattr("app.treasury.utc_now", lambda: fixed_now)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=42,
            issue_url="https://github.com/ramimbo/mergework/issues/42",
            title="Boundary reserve",
            reward_mrwk="1",
            acceptance="Accepted work.",
        )
        entry = session.scalar(
            select(LedgerEntry).where(
                LedgerEntry.entry_type == "bounty_reserve",
                LedgerEntry.reference == "https://github.com/ramimbo/mergework/issues/42",
            )
        )
        assert entry is not None
        entry.created_at = boundary_time

    response = client.get("/api/v1/treasury/status")

    assert response.status_code == 200
    body = response.json()
    assert body["executed_reserve_24h_mrwk"] == "1"
    assert body["recent_reserves"] == [
        {
            "ledger_sequence": entry.sequence,
            "amount_mrwk": "1",
            "reference": "https://github.com/ramimbo/mergework/issues/42",
            "created_at": public_utc_timestamp(boundary_time),
            "expires_at": public_utc_timestamp(fixed_now),
        }
    ]
    assert body["next_capacity_release_at"] is None


def test_treasury_status_projects_pending_create_capacity_events(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_now = utc_now().replace(microsecond=0)
    monkeypatch.setattr("app.treasury.utc_now", lambda: fixed_now)
    client = _client(sqlite_url, monkeypatch)
    recent_time = fixed_now - timedelta(hours=1)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=201,
            issue_url="https://github.com/ramimbo/mergework/issues/201",
            title="Recent reserve",
            reward_mrwk="8000",
            acceptance="Accepted work.",
        )
        recent_entry = session.scalar(
            select(LedgerEntry).where(
                LedgerEntry.entry_type == "bounty_reserve",
                LedgerEntry.reference == "https://github.com/ramimbo/mergework/issues/201",
            )
        )
        assert recent_entry is not None
        recent_entry.created_at = recent_time

    proposal = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json={**_bounty_payload(issue_number=202, reward_mrwk="1000"), "max_awards": 1},
    )
    response = client.get("/api/v1/treasury/status")

    assert proposal.status_code == 200
    assert response.status_code == 200
    body = response.json()
    pending_release_at = fixed_now + timedelta(hours=48)
    assert body["available_create_reserve_mrwk"] == "1000"
    assert body["pending_create_bounties"][0]["capacity_releases_at"] == (
        public_utc_timestamp(pending_release_at)
    )
    assert body["projected_capacity_events"] == [
        {
            "at": public_utc_timestamp(recent_time + timedelta(hours=24)),
            "event_type": "recent_reserve_releases",
            "amount_mrwk": "8000",
            "available_create_reserve_mrwk": "9000",
            "note": "Executed reserve leaves the 24h cap window.",
        },
        {
            "at": public_utc_timestamp(fixed_now + timedelta(hours=24)),
            "event_type": "pending_create_executes",
            "amount_mrwk": "1000",
            "available_create_reserve_mrwk": "9000",
            "note": "Pending create reserve becomes executed reserve; capacity does not increase.",
        },
        {
            "at": public_utc_timestamp(pending_release_at),
            "event_type": "pending_create_releases",
            "amount_mrwk": "1000",
            "available_create_reserve_mrwk": "10000",
            "note": "Executed reserve from this pending create leaves the 24h cap window.",
        },
    ]
    assert body["next_projected_capacity_release_at"] == public_utc_timestamp(
        recent_time + timedelta(hours=24)
    )


def test_treasury_status_clamps_overdue_pending_create_projection_to_now(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_now = utc_now().replace(microsecond=0)
    monkeypatch.setattr("app.treasury.utc_now", lambda: fixed_now)
    client = _client(sqlite_url, monkeypatch)
    proposal = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json={**_bounty_payload(issue_number=203, reward_mrwk="1000"), "max_awards": 1},
    )
    assert proposal.status_code == 200
    with session_scope(sqlite_url) as session:
        stored = session.get(TreasuryProposal, proposal.json()["id"])
        assert stored is not None
        stored.executes_after = fixed_now - timedelta(hours=2)

    response = client.get("/api/v1/treasury/status")

    assert response.status_code == 200
    body = response.json()
    pending_release_at = fixed_now + timedelta(hours=24)
    assert body["pending_create_bounties"][0]["executes_after"] == public_utc_timestamp(
        fixed_now - timedelta(hours=2)
    )
    assert body["pending_create_bounties"][0]["capacity_releases_at"] == (
        public_utc_timestamp(pending_release_at)
    )
    assert body["projected_capacity_events"] == [
        {
            "at": public_utc_timestamp(fixed_now),
            "event_type": "pending_create_executes",
            "amount_mrwk": "1000",
            "available_create_reserve_mrwk": "9000",
            "note": "Pending create reserve becomes executed reserve; capacity does not increase.",
        },
        {
            "at": public_utc_timestamp(pending_release_at),
            "event_type": "pending_create_releases",
            "amount_mrwk": "1000",
            "available_create_reserve_mrwk": "10000",
            "note": "Executed reserve from this pending create leaves the 24h cap window.",
        },
    ]
    assert body["next_projected_capacity_release_at"] == public_utc_timestamp(pending_release_at)


def test_treasury_proposals_list_honors_limit(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    first = client.post(
        "/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload(issue_number=72)
    ).json()
    second = client.post(
        "/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload(issue_number=73)
    ).json()

    limited = client.get("/api/v1/treasury/proposals?limit=1")
    too_small = client.get("/api/v1/treasury/proposals?limit=0")
    too_large = client.get("/api/v1/treasury/proposals?limit=201")
    controlled_limit = client.get("/api/v1/treasury/proposals?limit=%C2%8550")

    assert limited.status_code == 200
    assert [proposal["id"] for proposal in limited.json()] == [second["id"]]
    assert first["id"] not in [proposal["id"] for proposal in limited.json()]
    assert too_small.status_code == 422
    assert too_large.status_code == 422
    assert controlled_limit.status_code == 400
    assert controlled_limit.json()["detail"] == "limit must not contain control characters"


def test_treasury_proposals_list_filters_by_recipient_before_limit(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=74,
            issue_url="https://github.com/ramimbo/mergework/issues/74",
            title="Recipient filter proposal",
            reward_mrwk="5",
            max_awards=2,
            acceptance="Contributor comments with proof.",
        )
        bounty_id = bounty.id

    alice = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": "github:alice",
            "submission_url": "https://github.com/ramimbo/mergework/pull/7401",
            "accepted_by": "maintainer",
        },
    )
    bob = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": "github:bob",
            "submission_url": "https://github.com/ramimbo/mergework/pull/7402",
            "accepted_by": "maintainer",
        },
    )

    filtered = client.get(
        "/api/v1/treasury/proposals"
        "?status=pending&action=pay_bounty&to_account=github%3Aalice&limit=1"
    )
    uppercase_recipient_filtered = client.get(
        "/api/v1/treasury/proposals",
        params={
            "status": "pending",
            "action": "pay_bounty",
            "to_account": " GitHub:Alice ",
            "limit": "1",
        },
    )
    bob_filtered = client.get(
        "/api/v1/treasury/proposals"
        "?status=pending&action=pay_bounty&to_account=github%3Abob&limit=1"
    )

    assert alice.status_code == 200
    assert bob.status_code == 200
    assert filtered.status_code == 200
    assert [proposal["id"] for proposal in filtered.json()] == [alice.json()["id"]]
    assert filtered.json()[0]["payload"]["to_account"] == "github:alice"
    assert uppercase_recipient_filtered.status_code == 200
    assert uppercase_recipient_filtered.json() == filtered.json()
    assert bob_filtered.status_code == 200
    assert [proposal["id"] for proposal in bob_filtered.json()] == [bob.json()["id"]]


@pytest.mark.parametrize(
    ("query", "detail"),
    [
        ("to_account=%20%20", "to_account must not be blank"),
        (
            "to_account=github%3Aalice%0Abad",
            "to_account must not contain control characters",
        ),
        ("to_account=github%3A%20", "github login must be valid"),
    ],
)
def test_treasury_proposals_list_rejects_invalid_recipient_filter(
    sqlite_url: str,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    detail: str,
) -> None:
    response = _client(sqlite_url, monkeypatch).get(f"/api/v1/treasury/proposals?{query}")

    assert response.status_code == 400
    assert response.json()["detail"] == detail


def test_treasury_proposals_list_filters_by_action_status_and_bounty_id(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        first_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=90,
            issue_url="https://github.com/ramimbo/mergework/issues/90",
            title="First filtered payout",
            reward_mrwk="5",
            acceptance="Contributor comments with proof.",
        )
        second_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=91,
            issue_url="https://github.com/ramimbo/mergework/issues/91",
            title="Second filtered payout",
            reward_mrwk="5",
            acceptance="Contributor comments with proof.",
        )
        first_bounty_id = first_bounty.id
        second_bounty_id = second_bounty.id

    first_payout = client.post(
        f"/api/v1/bounties/{first_bounty_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": "github:alice",
            "submission_url": "https://github.com/ramimbo/mergework/pull/90",
            "accepted_by": "maintainer",
        },
    ).json()
    second_payout = client.post(
        f"/api/v1/bounties/{second_bounty_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": "github:bob",
            "submission_url": "https://github.com/ramimbo/mergework/pull/91",
            "accepted_by": "maintainer",
        },
    ).json()
    create_bounty_proposal = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json=_bounty_payload(issue_number=92),
    ).json()
    _make_executable(sqlite_url, first_payout["id"])
    executed = client.post(
        f"/api/v1/treasury/proposals/{first_payout['id']}/execute",
        headers=ADMIN_HEADERS,
    )
    assert executed.status_code == 200

    action_filtered = client.get("/api/v1/treasury/proposals?action=pay_bounty")
    uppercase_action_filtered = client.get("/api/v1/treasury/proposals?action=PAY_BOUNTY")
    pending_filtered = client.get("/api/v1/treasury/proposals?status=pending")
    uppercase_pending_filtered = client.get("/api/v1/treasury/proposals?status=PENDING")
    bounty_filtered = client.get(f"/api/v1/treasury/proposals?bounty_id={first_bounty_id}")
    composed_filtered = client.get(
        "/api/v1/treasury/proposals",
        params={
            "action": "pay_bounty",
            "status": "pending",
            "bounty_id": second_bounty_id,
        },
    )
    limited_after_filter = client.get("/api/v1/treasury/proposals?action=pay_bounty&limit=1")

    assert action_filtered.status_code == 200
    assert [proposal["id"] for proposal in action_filtered.json()] == [
        second_payout["id"],
        first_payout["id"],
    ]
    assert uppercase_action_filtered.status_code == 200
    assert uppercase_action_filtered.json() == action_filtered.json()
    assert pending_filtered.status_code == 200
    assert [proposal["id"] for proposal in pending_filtered.json()] == [
        create_bounty_proposal["id"],
        second_payout["id"],
    ]
    assert uppercase_pending_filtered.status_code == 200
    assert uppercase_pending_filtered.json() == pending_filtered.json()
    assert bounty_filtered.status_code == 200
    assert [proposal["id"] for proposal in bounty_filtered.json()] == [first_payout["id"]]
    assert composed_filtered.status_code == 200
    assert [proposal["id"] for proposal in composed_filtered.json()] == [second_payout["id"]]
    assert limited_after_filter.status_code == 200
    assert [proposal["id"] for proposal in limited_after_filter.json()] == [second_payout["id"]]


@pytest.mark.parametrize(
    ("field", "value", "expected_detail"),
    (
        ("action", "\tpay_bounty", "action must not contain control characters"),
        ("bounty_id", "\x8599", "bounty_id must not contain control characters"),
        ("bounty_id", "99.0", "bounty_id must be a canonical positive integer"),
        ("bounty_id", "+99", "bounty_id must be a canonical positive integer"),
        ("bounty_id", "099", "bounty_id must be a canonical positive integer"),
        ("limit", "1.0", "limit must be a canonical positive integer"),
        ("limit", "+1", "limit must be a canonical positive integer"),
        ("limit", "01", "limit must be a canonical positive integer"),
        ("status", " ", "status is required"),
        ("action", "paybounty", "action must be one of: close_bounty, create_bounty, pay_bounty"),
        ("status", "complete", "status must be one of: pending, executed, blocked"),
    ),
)
def test_treasury_proposals_list_rejects_invalid_filters(
    sqlite_url: str,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    expected_detail: str,
) -> None:
    client = _client(sqlite_url, monkeypatch)

    response = client.get("/api/v1/treasury/proposals", params={field: value})

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


@pytest.mark.parametrize(
    ("query", "detail"),
    [
        ("limit=not-an-int&limit=2", "limit must be provided at most once"),
        ("status=invalid&status=pending", "status must be provided at most once"),
        ("action=invalid&action=pay_bounty", "action must be provided at most once"),
        ("to_account=bad&to_account=github%3Aalice", "to_account must be provided at most once"),
        ("bounty_id=not-an-int&bounty_id=1", "bounty_id must be provided at most once"),
    ],
)
def test_treasury_proposals_list_rejects_repeated_scalar_filters(
    sqlite_url: str,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    detail: str,
) -> None:
    response = _client(sqlite_url, monkeypatch).get(f"/api/v1/treasury/proposals?{query}")

    assert response.status_code == 400
    assert response.json()["detail"] == detail


def test_direct_proposal_creation_requires_admin_token(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    payload = {"action": "create_bounty", "payload": _bounty_payload(issue_number=78)}

    unauthenticated = client.post("/api/v1/treasury/proposals", json=payload)
    authorized = client.post("/api/v1/treasury/proposals", headers=ADMIN_HEADERS, json=payload)

    assert unauthenticated.status_code == 401
    assert authorized.status_code == 200
    body = authorized.json()
    assert body["action"] == "create_bounty"
    assert body["status"] == "pending"
    assert body["payload"]["issue_number"] == 78


@pytest.mark.parametrize(
    ("body", "expected_detail"),
    [
        (
            {"action": "\tcreate_bounty", "payload": _bounty_payload(issue_number=179)},
            "action must not contain control characters",
        ),
        (
            {
                "action": "create_bounty",
                "payload": {**_bounty_payload(issue_number=180), "issue_number": "\t180"},
            },
            "issue_number must not contain control characters",
        ),
        (
            {
                "action": "create_bounty",
                "payload": {**_bounty_payload(issue_number=181), "title": "Proposal\x85Title"},
            },
            "title must not contain control characters",
        ),
    ],
)
def test_treasury_proposal_creation_rejects_raw_control_characters(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch, body: dict[str, object], expected_detail: str
) -> None:
    client = _client(sqlite_url, monkeypatch)

    response = client.post("/api/v1/treasury/proposals", headers=ADMIN_HEADERS, json=body)

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 0


def test_proposal_execution_requires_admin_delay_and_is_idempotent(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    proposal = client.post("/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload()).json()

    unauthenticated = client.post(f"/api/v1/treasury/proposals/{proposal['id']}/execute")
    too_early = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )
    _make_executable(sqlite_url, proposal["id"])
    executed = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )
    duplicate = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )

    assert unauthenticated.status_code == 401
    assert too_early.status_code == 400
    assert too_early.json()["detail"] == "proposal delay has not elapsed"
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed"
    assert executed.json()["result"]["bounty"]["issue_number"] == 77
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "proposal already executed"


def test_create_bounty_execution_records_github_issue_finalization(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def fake_finalize_created_bounty_issue(
        *,
        github_token: str,
        public_base_url: str,
        bounty: dict[str, object],
    ) -> dict[str, object]:
        calls.append(
            {
                "github_token": github_token,
                "public_base_url": public_base_url,
                "bounty": bounty,
            }
        )
        return {
            "status": "updated",
            "label": "mrwk:bounty",
            "comment_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-1",
        }

    monkeypatch.setenv("MERGEWORK_GITHUB_ISSUE_TOKEN", "github-issue-token-for-tests")
    monkeypatch.setenv("MERGEWORK_PUBLIC_BASE_URL", "https://mrwk.example")
    monkeypatch.setattr(
        "app.treasury_executor.finalize_created_bounty_issue",
        fake_finalize_created_bounty_issue,
    )
    client = _client(sqlite_url, monkeypatch)
    proposal = client.post("/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload()).json()
    _make_executable(sqlite_url, proposal["id"])

    executed = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )

    assert executed.status_code == 200
    expected_finalization = {
        "status": "updated",
        "label": "mrwk:bounty",
        "comment_url": "https://github.com/ramimbo/mergework/issues/77#issuecomment-1",
    }
    finalization = executed.json()["result"]["github_issue_finalization"]
    assert finalization == expected_finalization
    assert len(calls) == 1
    assert calls[0]["github_token"] == "github-issue-token-for-tests"
    assert calls[0]["public_base_url"] == "https://mrwk.example"
    bounty = calls[0]["bounty"]
    assert isinstance(bounty, dict)
    assert bounty["issue_url"] == "https://github.com/ramimbo/mergework/issues/77"
    stored_result = _stored_proposal_result(sqlite_url, proposal["id"])
    assert stored_result["github_issue_finalization"] == expected_finalization


def test_create_bounty_execution_persists_skipped_github_issue_finalization(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_finalize_created_bounty_issue(
        *,
        github_token: str,
        public_base_url: str,
        bounty: dict[str, object],
    ) -> dict[str, object]:
        assert github_token == ""
        assert public_base_url == "https://mrwk.example"
        assert bounty["issue_number"] == 75
        return {"status": "skipped", "reason": "github issue token not configured"}

    monkeypatch.setattr(
        "app.treasury_executor.finalize_created_bounty_issue",
        fake_finalize_created_bounty_issue,
    )
    monkeypatch.setenv("MERGEWORK_PUBLIC_BASE_URL", "https://mrwk.example")
    client = _client(sqlite_url, monkeypatch)
    proposal = client.post(
        "/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload(issue_number=75)
    ).json()
    _make_executable(sqlite_url, proposal["id"])

    executed = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )

    assert executed.status_code == 200
    finalization = executed.json()["result"]["github_issue_finalization"]
    expected_finalization = {
        "status": "skipped",
        "reason": "github issue token not configured",
    }
    assert finalization == expected_finalization
    stored_result = _stored_proposal_result(sqlite_url, proposal["id"])
    assert stored_result["github_issue_finalization"] == expected_finalization


def test_create_bounty_execution_does_not_block_on_github_issue_finalization_error(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_finalize_created_bounty_issue(
        *,
        github_token: str,
        public_base_url: str,
        bounty: dict[str, object],
    ) -> dict[str, object]:
        raise RuntimeError("github unavailable")

    monkeypatch.setenv("MERGEWORK_GITHUB_ISSUE_TOKEN", "github-issue-token-for-tests")
    monkeypatch.setattr(
        "app.treasury_executor.finalize_created_bounty_issue",
        broken_finalize_created_bounty_issue,
    )
    client = _client(sqlite_url, monkeypatch)
    proposal = client.post(
        "/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload(issue_number=76)
    ).json()
    _make_executable(sqlite_url, proposal["id"])

    executed = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )

    assert executed.status_code == 200
    body = executed.json()
    assert body["status"] == "executed"
    assert body["result"]["bounty"]["issue_number"] == 76
    expected_finalization = {
        "status": "failed",
        "reason": "github issue finalization failed: RuntimeError",
    }
    assert body["result"]["github_issue_finalization"] == expected_finalization
    stored_result = _stored_proposal_result(sqlite_url, proposal["id"])
    assert stored_result["github_issue_finalization"] == expected_finalization


def test_proposal_execution_rejects_payload_hash_mismatch(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    proposal = client.post("/api/v1/bounties", headers=ADMIN_HEADERS, json=_bounty_payload()).json()
    tampered_payload = dict(proposal["payload"])
    tampered_payload["reward_mrwk"] = "500"
    with session_scope(sqlite_url) as session:
        stored = session.get(TreasuryProposal, proposal["id"])
        assert stored is not None
        stored.payload_json = canonical_json(tampered_payload)
        stored.executes_after = utc_now() - timedelta(seconds=1)

    response = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "proposal payload hash mismatch"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(Bounty.id))) == 0


def test_create_bounty_execution_enforces_epoch_reserve_cap(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=1,
            issue_url="https://github.com/ramimbo/mergework/issues/1",
            title="Existing large reserve",
            reward_mrwk="9000",
            acceptance="Accepted work.",
        )
    response = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json=_bounty_payload(issue_number=2, reward_mrwk="1001"),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "treasury epoch reserve cap exceeded"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(Bounty.id))) == 1
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 0


def test_create_bounty_proposal_rejects_mismatched_issue_url(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    payload = _bounty_payload(issue_number=82)
    payload["issue_url"] = "https://github.com/not-the/repo/issues/1"

    response = client.post("/api/v1/bounties", headers=ADMIN_HEADERS, json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "issue_url must match repo and issue_number"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 0


def test_duplicate_pending_create_bounty_proposal_is_rejected(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    first = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json=_bounty_payload(issue_number=83),
    )

    duplicate = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json=_bounty_payload(issue_number=83),
    )

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "create_bounty proposal already pending"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 1


def test_pending_create_bounty_proposals_count_toward_epoch_cap(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    first = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json=_bounty_payload(issue_number=84, reward_mrwk="6000"),
    )

    over_cap = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json=_bounty_payload(issue_number=85, reward_mrwk="5000"),
    )

    assert first.status_code == 200
    assert over_cap.status_code == 400
    assert over_cap.json()["detail"] == "treasury epoch reserve cap exceeded"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 1


def test_manual_payout_creates_proposal_then_executes_after_delay(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=12,
            issue_url="https://github.com/ramimbo/mergework/issues/12",
            title="Manual payout proposal",
            reward_mrwk="15",
            acceptance="Contributor comments with proof.",
        )
        wallet = register_wallet(session, public_key_hex="11" * 32, label="Contributor")
        bounty_id = bounty.id
        wallet_address = wallet.address

    proposal = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": wallet_address,
            "submission_url": "https://github.com/ramimbo/mergework/issues/12#issuecomment-1",
            "accepted_by": "maintainer",
        },
    )

    assert proposal.status_code == 200
    assert proposal.json()["action"] == "pay_bounty"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, wallet_address) == 0

    _make_executable(sqlite_url, proposal.json()["id"])
    executed = client.post(
        f"/api/v1/treasury/proposals/{proposal.json()['id']}/execute", headers=ADMIN_HEADERS
    )

    assert executed.status_code == 200
    assert executed.json()["result"]["payout"]["to_account"] == wallet_address
    assert executed.json()["result"]["payout"]["proof_url"].startswith("/proofs/")
    with session_scope(sqlite_url) as session:
        assert get_balance(session, wallet_address) == 15_000_000
        assert verify_hash_chain(session) is True
        assert verify_supply_conservation(session) is True


def test_manual_payout_rejects_invalid_target_before_proposal_creation(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=13,
            issue_url="https://github.com/ramimbo/mergework/issues/13",
            title="Manual payout invalid target",
            reward_mrwk="15",
            acceptance="Contributor comments with proof.",
        )
        bounty_id = bounty.id

    response = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": "not-a-valid-ledger-account",
            "submission_url": "https://github.com/ramimbo/mergework/issues/13#issuecomment-1",
            "accepted_by": "maintainer",
        },
    )

    assert response.status_code == 400
    assert (
        response.json()["detail"]
        == "to_account must be a github:<login> account or registered mrwk1 wallet"
    )
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 0


def test_manual_payout_rejects_missing_bounty_before_proposal_creation(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)

    response = client.post(
        "/api/v1/bounties/999/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": "github:bob",
            "submission_url": "https://github.com/ramimbo/mergework/pull/999",
            "accepted_by": "maintainer",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "bounty not found"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 0


def test_manual_payout_rejects_existing_unpaid_submission_before_proposal_creation(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    submission_url = "https://github.com/ramimbo/mergework/issues/81#issuecomment-1"
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=81,
            issue_url="https://github.com/ramimbo/mergework/issues/81",
            title="Manual payout with existing unpaid submission",
            reward_mrwk="10",
            acceptance="Contributor comments with proof.",
        )
        session.add(
            Submission(
                bounty_id=bounty.id,
                submitter_account="github:bob",
                url=submission_url,
            )
        )
        bounty_id = bounty.id

    response = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": "github:bob",
            "submission_url": submission_url,
            "accepted_by": "maintainer",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "submission already paid"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 0


def test_manual_payout_rejects_duplicate_pending_submission_proposal(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=86,
            issue_url="https://github.com/ramimbo/mergework/issues/86",
            title="Duplicate pending payout proposal",
            reward_mrwk="5",
            max_awards=2,
            acceptance="Contributor comments with proof.",
        )
        bounty_id = bounty.id
    payload = {
        "to_account": "github:bob",
        "submission_url": "https://github.com/ramimbo/mergework/pull/86",
        "accepted_by": "maintainer",
    }
    first = client.post(f"/api/v1/bounties/{bounty_id}/pay", headers=ADMIN_HEADERS, json=payload)

    duplicate = client.post(
        f"/api/v1/bounties/{bounty_id}/pay", headers=ADMIN_HEADERS, json=payload
    )

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "pay_bounty proposal already pending for submission"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 1


def test_manual_payout_rejects_pending_capacity_overcommit(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=87,
            issue_url="https://github.com/ramimbo/mergework/issues/87",
            title="Pending payout capacity",
            reward_mrwk="5",
            acceptance="Contributor comments with proof.",
        )
        bounty_id = bounty.id
    first = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": "github:alice",
            "submission_url": "https://github.com/ramimbo/mergework/pull/87",
            "accepted_by": "maintainer",
        },
    )

    second = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": "github:bob",
            "submission_url": "https://github.com/ramimbo/mergework/pull/88",
            "accepted_by": "maintainer",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["detail"] == "pending payout proposals exceed bounty remaining awards"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 1


def test_manual_payout_freezes_github_destination_at_proposal_creation(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=88,
            issue_url="https://github.com/ramimbo/mergework/issues/88",
            title="Frozen payout destination",
            reward_mrwk="5",
            acceptance="Contributor comments with proof.",
        )
        bounty_id = bounty.id
    proposal = client.post(
        f"/api/v1/bounties/{bounty_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": "github:bob",
            "submission_url": "https://github.com/ramimbo/mergework/pull/88",
            "accepted_by": "maintainer",
        },
    )
    assert proposal.status_code == 200
    assert proposal.json()["payload"]["to_account"] == "github:bob"
    with session_scope(sqlite_url) as session:
        wallet = register_wallet(session, public_key_hex="22" * 32, label="Bob")
        wallet.github_login = "bob"
        wallet_address = wallet.address

    _make_executable(sqlite_url, proposal.json()["id"])
    executed = client.post(
        f"/api/v1/treasury/proposals/{proposal.json()['id']}/execute", headers=ADMIN_HEADERS
    )

    assert executed.status_code == 200
    assert executed.json()["result"]["payout"]["to_account"] == "github:bob"
    with session_scope(sqlite_url) as session:
        assert get_balance(session, "github:bob") == 5_000_000
        assert get_balance(session, wallet_address) == 0


def test_challenges_require_accepted_work_and_can_block_invalid_proposals(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        _seed_accepted_work(session, "alice")
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=21,
            issue_url="https://github.com/ramimbo/mergework/issues/21",
            title="Existing bounty",
            reward_mrwk="1",
            acceptance="Accepted proof.",
        )
    proposal = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json=_bounty_payload(issue_number=21, reward_mrwk="2"),
    ).json()

    unauthenticated = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/challenges",
        json={"challenge_type": "subjective_note", "reason": "Needs more explanation."},
    )
    client.cookies.set("mrwk_user", signed_value("bob", "test-cookie-secret"))
    unearned = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/challenges",
        json={"challenge_type": "subjective_note", "reason": "Needs more explanation."},
    )
    client.cookies.set("mrwk_user", signed_value("alice", "test-cookie-secret"))
    subjective = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/challenges",
        json={"challenge_type": "subjective_note", "reason": "Is this a good move?"},
    )
    blocking = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/challenges",
        json={"challenge_type": "duplicate_bounty", "reason": "Issue already has a bounty."},
    )
    _make_executable(sqlite_url, proposal["id"])
    execute = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )

    assert unauthenticated.status_code == 401
    assert unearned.status_code == 403
    assert unearned.json()["detail"] == "accepted MRWK work required to challenge proposals"
    assert subjective.status_code == 200
    assert subjective.json()["status"] == "noted"
    assert blocking.status_code == 200
    assert blocking.json()["status"] == "accepted_blocking"
    assert execute.status_code == 400
    assert execute.json()["detail"] == "proposal has blocking challenge"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryChallenge.id))) == 2


def test_machine_challenge_after_execution_is_rejected_without_mutating_status(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        _seed_accepted_work(session, "alice")
    proposal = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json=_bounty_payload(issue_number=80, reward_mrwk="2"),
    ).json()
    _make_executable(sqlite_url, proposal["id"])
    executed = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/execute", headers=ADMIN_HEADERS
    )
    client.cookies.set("mrwk_user", signed_value("alice", "test-cookie-secret"))

    challenge = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/challenges",
        json={"challenge_type": "duplicate_bounty", "reason": "Issue already has a bounty."},
    )
    detail = client.get(f"/api/v1/treasury/proposals/{proposal['id']}")

    assert executed.status_code == 200
    assert executed.json()["status"] == "executed"
    assert challenge.status_code == 200
    assert challenge.json()["status"] == "rejected"
    assert detail.status_code == 200
    assert detail.json()["status"] == "executed"
    assert detail.json()["executed_at"] is not None


@pytest.mark.parametrize(
    ("challenge_body", "expected_detail"),
    [
        (
            {"challenge_type": "\tsubjective_note", "reason": "Needs more explanation."},
            "challenge_type must not contain control characters",
        ),
        (
            {"challenge_type": "subjective_note", "reason": "\x85Needs more explanation."},
            "reason must not contain control characters",
        ),
    ],
)
def test_treasury_challenges_reject_raw_control_characters(
    sqlite_url: str,
    monkeypatch: pytest.MonkeyPatch,
    challenge_body: dict[str, str],
    expected_detail: str,
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        _seed_accepted_work(session, "alice")
    proposal = client.post(
        "/api/v1/bounties",
        headers=ADMIN_HEADERS,
        json=_bounty_payload(issue_number=81, reward_mrwk="2"),
    ).json()
    client.cookies.set("mrwk_user", signed_value("alice", "test-cookie-secret"))

    response = client.post(
        f"/api/v1/treasury/proposals/{proposal['id']}/challenges",
        json=challenge_body,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryChallenge.id))) == 0


def test_close_bounty_rejects_missing_bounty_before_proposal_creation(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)

    response = client.post(
        "/api/v1/bounties/999/close",
        headers=ADMIN_HEADERS,
        json={"closed_by": "maintainer"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "bounty not found"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 0


def test_close_bounty_rejects_duplicate_pending_close_proposal(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=89,
            issue_url="https://github.com/ramimbo/mergework/issues/89",
            title="Duplicate pending close proposal",
            reward_mrwk="5",
            acceptance="Contributor comments with proof.",
        )
        bounty_id = bounty.id
    first = client.post(
        f"/api/v1/bounties/{bounty_id}/close",
        headers=ADMIN_HEADERS,
        json={"closed_by": "maintainer"},
    )

    duplicate = client.post(
        f"/api/v1/bounties/{bounty_id}/close",
        headers=ADMIN_HEADERS,
        json={"closed_by": "maintainer"},
    )

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "close_bounty proposal already pending"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 1


def test_pending_payout_and_close_proposals_are_mutually_exclusive(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(sqlite_url, monkeypatch)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        payout_first = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=90,
            issue_url="https://github.com/ramimbo/mergework/issues/90",
            title="Pending payout before close",
            reward_mrwk="5",
            acceptance="Contributor comments with proof.",
        )
        close_first = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=91,
            issue_url="https://github.com/ramimbo/mergework/issues/91",
            title="Pending close before payout",
            reward_mrwk="5",
            acceptance="Contributor comments with proof.",
        )
        payout_first_id = payout_first.id
        close_first_id = close_first.id
    payout = client.post(
        f"/api/v1/bounties/{payout_first_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": "github:bob",
            "submission_url": "https://github.com/ramimbo/mergework/pull/90",
            "accepted_by": "maintainer",
        },
    )
    close_after_payout = client.post(
        f"/api/v1/bounties/{payout_first_id}/close",
        headers=ADMIN_HEADERS,
        json={"closed_by": "maintainer"},
    )
    close = client.post(
        f"/api/v1/bounties/{close_first_id}/close",
        headers=ADMIN_HEADERS,
        json={"closed_by": "maintainer"},
    )
    payout_after_close = client.post(
        f"/api/v1/bounties/{close_first_id}/pay",
        headers=ADMIN_HEADERS,
        json={
            "to_account": "github:bob",
            "submission_url": "https://github.com/ramimbo/mergework/pull/91",
            "accepted_by": "maintainer",
        },
    )

    assert payout.status_code == 200
    assert close_after_payout.status_code == 409
    assert close_after_payout.json()["detail"] == "bounty has pending payout proposals"
    assert close.status_code == 200
    assert payout_after_close.status_code == 409
    assert payout_after_close.json()["detail"] == "bounty has pending close proposal"


@pytest.mark.parametrize("action", ("pay_bounty", "close_bounty"))
def test_direct_pay_and_close_proposals_reject_oversized_bounty_ids(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
        raise_server_exceptions=False,
    )
    payload: dict[str, object]
    if action == "pay_bounty":
        payload = {
            "bounty_id": SQLITE_INTEGER_MAX + 1,
            "to_account": "github:bob",
            "submission_url": "https://github.com/ramimbo/mergework/pull/9223372036854775808",
            "accepted_by": "maintainer",
        }
    else:
        payload = {
            "bounty_id": SQLITE_INTEGER_MAX + 1,
            "closed_by": "maintainer",
        }

    response = client.post(
        "/api/v1/treasury/proposals",
        headers=ADMIN_HEADERS,
        json={"action": action, "payload": payload},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "bounty id is too large"
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(TreasuryProposal.id))) == 0
