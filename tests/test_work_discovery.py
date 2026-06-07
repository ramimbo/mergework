from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from app import work_discovery
from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty
from app.main import create_app
from app.treasury import propose_treasury_action


def test_work_discovery_distinguishes_live_and_pending_create_work(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        live_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=800,
            issue_url="https://github.com/ramimbo/mergework/issues/800",
            title="MRWK bounty: public work discovery",
            reward_mrwk="600",
            max_awards=1,
            acceptance="Expose public work discovery data.",
        )
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=761,
            issue_url="https://github.com/ramimbo/mergework/issues/761",
            title="Filled bounty round",
            reward_mrwk="150",
            max_awards=1,
            acceptance="Already filled.",
        )
        pay_bounty(
            session,
            bounty_id=paid_bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/821",
            accepted_by="maintainer",
            verifier_result={"source": "test"},
        )
        pending_create = propose_treasury_action(
            session,
            action="create_bounty",
            payload={
                "repo": "ramimbo/mergework",
                "issue_number": 900,
                "issue_url": "https://github.com/ramimbo/mergework/issues/900",
                "title": "Opening soon bounty",
                "reward_mrwk": "75",
                "max_awards": 2,
                "acceptance": "Pending create proposal should be opening soon.",
            },
            proposed_by="maintainer",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/api/v1/work-discovery")

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "work_discovery"
    assert body["summary"] == {
        "claimable_now_count": 1,
        "opening_soon_count": 1,
        "not_claimable_count": 1,
        "limit": 50,
    }
    assert body["state_definitions"]["live_bounty"] == (
        "Public bounty row is open and has positive effective_awards_remaining."
    )
    assert body["state_definitions"]["pending_create"] == (
        "Public treasury proposal exists but the bounty row is not live yet."
    )
    assert body["state_definitions"]["board_or_index"] == (
        "Index issues help discovery but are not claimable bounty work."
    )
    assert body["non_claimable_issue_states"][0]["availability_state"] == "proposed_work"
    assert body["non_claimable_issue_states"][1]["availability_state"] == "board_or_index"
    assert body["non_claimable_issue_states"][1]["issue_number"] == 785

    live_requirements = body["claimable_now"][0]["submission_requirements"]
    assert live_requirements["reference_formats"] == ["Bounty #800", "Refs #800"]
    assert live_requirements["attempt_endpoint"] == f"/api/v1/bounties/{live_bounty.id}/attempts"
    assert live_requirements["next_actions"][0]["id"] == "confirm_award_slot"

    assert body["claimable_now"] == [
        {
            "availability_state": "live_bounty",
            "bounty_id": live_bounty.id,
            "repo": "ramimbo/mergework",
            "issue_number": 800,
            "title": "MRWK bounty: public work discovery",
            "issue_url": "https://github.com/ramimbo/mergework/issues/800",
            "reward_mrwk": "600",
            "max_awards": 1,
            "effective_awards_remaining": 1,
            "bounty_availability_state": "open",
            "pending_payout_awards": 0,
            "source_urls": {
                "bounty": f"/api/v1/bounties/{live_bounty.id}",
                "attempts": f"/api/v1/bounties/{live_bounty.id}/attempts",
                "github_issue": "https://github.com/ramimbo/mergework/issues/800",
            },
            "next_action": {
                "id": "confirm_award_slot",
                "required": True,
                "text": "Confirm this bounty is open and has at least one award slot remaining.",
            },
            "submission_requirements": live_requirements,
        }
    ]
    pending_create_executes_after = body["opening_soon"][0]["executes_after"]
    pending_create_requirements = body["opening_soon"][0]["submission_requirements"]
    assert pending_create_requirements["reference_formats"] == [
        "Bounty #900",
        "Refs #900",
    ]
    assert pending_create_requirements["attempt_endpoint"] == (
        "/api/v1/bounties/<bounty_id>/attempts"
    )
    assert pending_create_requirements["next_actions"][0]["id"] == "select_bounty"
    assert body["opening_soon"] == [
        {
            "availability_state": "pending_create",
            "proposal_id": pending_create.id,
            "repo": "ramimbo/mergework",
            "issue_number": 900,
            "title": "Opening soon bounty",
            "issue_url": "https://github.com/ramimbo/mergework/issues/900",
            "reward_mrwk": "75",
            "max_awards": 2,
            "effective_awards_remaining": 0,
            "executes_after": pending_create_executes_after,
            "source_urls": {
                "proposal": f"/api/v1/treasury/proposals/{pending_create.id}",
                "github_issue": "https://github.com/ramimbo/mergework/issues/900",
            },
            "next_action": {
                "id": "select_bounty",
                "required": True,
                "text": "Select a concrete open bounty before submitting work proof.",
            },
            "submission_requirements": pending_create_requirements,
        }
    ]
    assert pending_create_executes_after.endswith("Z")
    assert datetime.fromisoformat(pending_create_executes_after.replace("Z", "+00:00"))
    assert body["not_claimable"][0]["availability_state"] == "closed_or_exhausted"
    assert body["not_claimable"][0]["repo"] == "ramimbo/mergework"
    assert body["not_claimable"][0]["issue_number"] == 761
    assert body["not_claimable"][0]["next_action"]["id"] == "choose_open_bounty"


def test_work_discovery_limit_caps_public_buckets(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        for issue_number in (901, 902):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=issue_number,
                issue_url=f"https://github.com/ramimbo/mergework/issues/{issue_number}",
                title=f"Live bounty {issue_number}",
                reward_mrwk="10",
                acceptance="Live bounty.",
            )
        for issue_number in (903, 904):
            propose_treasury_action(
                session,
                action="create_bounty",
                payload={
                    "repo": "ramimbo/mergework",
                    "issue_number": issue_number,
                    "issue_url": f"https://github.com/ramimbo/mergework/issues/{issue_number}",
                    "title": f"Pending bounty {issue_number}",
                    "reward_mrwk": "5",
                    "max_awards": 1,
                    "acceptance": "Pending create.",
                },
                proposed_by="maintainer",
            )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    body = client.get("/api/v1/work-discovery?limit=1").json()

    assert body["summary"]["limit"] == 1
    assert len(body["claimable_now"]) == 1
    assert len(body["opening_soon"]) == 1


def test_work_discovery_limit_keeps_older_claimable_bounty_visible(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        older_live = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=910,
            issue_url="https://github.com/ramimbo/mergework/issues/910",
            title="Older live bounty",
            reward_mrwk="25",
            max_awards=1,
            acceptance="This live bounty should remain discoverable.",
        )
        newer_pending_full = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=911,
            issue_url="https://github.com/ramimbo/mergework/issues/911",
            title="Newer pending payout full bounty",
            reward_mrwk="30",
            max_awards=1,
            acceptance="A pending payout should consume effective capacity.",
        )
        propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": newer_pending_full.id,
                "to_account": "github:alice",
                "submission_url": "https://github.com/ramimbo/mergework/pull/911",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    body = client.get("/api/v1/work-discovery?limit=1").json()

    assert body["summary"]["limit"] == 1
    assert body["summary"]["claimable_now_count"] == 1
    assert body["claimable_now"][0]["bounty_id"] == older_live.id
    assert body["claimable_now"][0]["repo"] == "ramimbo/mergework"
    assert body["claimable_now"][0]["issue_number"] == 910
    assert body["claimable_now"][0]["availability_state"] == "live_bounty"
    assert body["not_claimable"][0]["bounty_id"] == newer_pending_full.id
    assert body["not_claimable"][0]["repo"] == "ramimbo/mergework"
    assert body["not_claimable"][0]["issue_number"] == 911
    assert body["not_claimable"][0]["availability_state"] == "pending_payout"
    assert body["not_claimable"][0]["next_action"]["id"] == "watch_for_award_slot"


def test_work_discovery_scans_open_bounties_in_bounded_pages(
    sqlite_url: str,
    monkeypatch,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        for issue_number in range(950, 990):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=issue_number,
                issue_url=f"https://github.com/ramimbo/mergework/issues/{issue_number}",
                title=f"Live bounty {issue_number}",
                reward_mrwk="10",
                max_awards=1,
                acceptance="Live bounty.",
            )
        newest_pending_full = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=990,
            issue_url="https://github.com/ramimbo/mergework/issues/990",
            title="Newest pending payout full bounty",
            reward_mrwk="30",
            max_awards=1,
            acceptance="Newest open row should fill the not-claimable bucket.",
        )
        propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": newest_pending_full.id,
                "to_account": "github:alice",
                "submission_url": "https://github.com/ramimbo/mergework/pull/990",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )

    batch_sizes: list[int] = []
    real_bounties_to_dict = work_discovery.bounties_to_dict

    def instrumented_bounties_to_dict(bounties, *, session):
        batch_sizes.append(len(bounties))
        return real_bounties_to_dict(bounties, session=session)

    monkeypatch.setattr(work_discovery, "bounties_to_dict", instrumented_bounties_to_dict)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    body = client.get("/api/v1/work-discovery?limit=1").json()

    assert body["claimable_now"][0]["issue_number"] == 989
    assert body["not_claimable"][0]["issue_number"] == 990
    assert batch_sizes == [work_discovery.OPEN_BOUNTY_SCAN_PAGE_SIZE]
