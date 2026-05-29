from __future__ import annotations

from datetime import timedelta

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
from app.models import Bounty, Submission, TreasuryChallenge, TreasuryProposal, utc_now
from app.path_params import SQLITE_INTEGER_MAX

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
    with session_scope(sqlite_url) as session:
        assert session.scalar(select(func.count(Bounty.id))) == 0
        assert get_balance(session, TREASURY_ACCOUNT) == 100_000_000_000_000

    listed = client.get("/api/v1/treasury/proposals")
    detail = client.get(f"/api/v1/treasury/proposals/{body['id']}")

    assert listed.status_code == 200
    assert [proposal["id"] for proposal in listed.json()] == [body["id"]]
    assert detail.status_code == 200
    assert detail.json()["payload_hash"] == body["payload_hash"]


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
