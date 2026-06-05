from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import close_bounty, create_bounty, ensure_genesis, pay_bounty
from app.main import create_app
from app.path_params import SQLITE_INTEGER_MAX
from app.treasury import propose_treasury_action


def test_bounty_api_reports_multi_award_capacity(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=11,
            issue_url="https://github.com/ramimbo/mergework/issues/11",
            title="Multi-award bounty",
            reward_mrwk="25",
            max_awards=4,
            acceptance="Each accepted submission earns one award.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    bounty = client.get("/api/v1/bounties").json()[0]

    assert bounty["reward_mrwk"] == "25"
    assert bounty["available_mrwk"] == "100"
    assert bounty["reserved_mrwk"] == "100"
    assert bounty["max_awards"] == 4
    assert bounty["awards_paid"] == 0
    assert bounty["awards_remaining"] == 4
    assert bounty["effective_available_mrwk"] == "100"
    assert bounty["effective_awards_remaining"] == 4
    assert bounty["pending_payout_awards"] == 0
    assert bounty["pending_close_proposal"] is None
    assert bounty["availability_state"] == "open"
    assert bounty["submission_requirements"]["submission_mode"] == "pr_or_evidence"
    assert bounty["submission_requirements"]["expected_artifact"] == (
        "focused PR, issue, report, or evidence URL"
    )


def test_bounty_api_reports_issue_submission_requirements(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=649,
            issue_url="https://github.com/ramimbo/mergework/issues/649",
            title="MRWK bounty: accepted proposed-work requests, round 1",
            reward_mrwk="50",
            max_awards=20,
            acceptance=(
                "Accepted work is a new proposed-work issue in ramimbo/mergework. "
                "Payment review uses the proposed-work issue URL as submission_url."
            ),
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    requirements = client.get(f"/api/v1/bounties/{bounty_id}").json()["submission_requirements"]

    assert requirements["submission_mode"] == "issue"
    assert requirements["submission_url_kind"] == "github_issue"
    assert requirements["expected_artifact"] == "new proposed-work GitHub issue URL"
    assert requirements["claim_command"] == "/claim #649"
    assert requirements["attempt_endpoint"] == f"/api/v1/bounties/{bounty_id}/attempts"
    assert requirements["attempt_endpoint_applicability"] == ("not_required_for_issue_submission")


def test_bounty_api_reports_pending_payout_effective_capacity(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=12,
            issue_url="https://github.com/ramimbo/mergework/issues/12",
            title="Pending payout capacity",
            reward_mrwk="40",
            max_awards=2,
            acceptance="Pending payouts should reduce effective public capacity.",
        )
        bounty_id = bounty.id
        propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": bounty_id,
                "to_account": "github:alice",
                "submission_url": "https://github.com/ramimbo/mergework/pull/12",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    body = client.get(f"/api/v1/bounties/{bounty_id}").json()
    summary = client.get("/api/v1/bounties/summary?status=open").json()

    assert body["awards_remaining"] == 2
    assert body["available_mrwk"] == "80"
    assert body["effective_awards_remaining"] == 1
    assert body["effective_available_mrwk"] == "40"
    assert body["pending_payout_awards"] == 1
    assert body["pending_payout_proposals"][0]["submission_url"] == (
        "https://github.com/ramimbo/mergework/pull/12"
    )
    assert body["availability_state"] == "pending_payouts_partial"
    assert "1 award covered by pending payout proposal" in body["availability_note"]
    assert summary["open_awards"] == 2
    assert summary["open_pool_mrwk"] == "80"
    assert summary["effective_open_awards"] == 1
    assert summary["effective_open_pool_mrwk"] == "40"
    assert summary["availability_state_counts"] == {"pending_payouts_partial": 1}
    assert summary["pending_payout_awards"] == 1
    assert summary["reduced_capacity_bounties"] == 1
    assert summary["effectively_unavailable_bounties"] == 0


def test_bounty_api_reports_pending_close_as_effectively_unavailable(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=13,
            issue_url="https://github.com/ramimbo/mergework/issues/13",
            title="Pending close capacity",
            reward_mrwk="15",
            max_awards=3,
            acceptance="Pending close proposals should make public capacity unavailable.",
        )
        bounty_id = bounty.id
        proposal = propose_treasury_action(
            session,
            action="close_bounty",
            payload={
                "bounty_id": bounty_id,
                "closed_by": "maintainer",
                "reference": "https://github.com/ramimbo/mergework/issues/13#close",
            },
            proposed_by="maintainer",
        )
        proposal_id = proposal.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    body = client.get(f"/api/v1/bounties/{bounty_id}").json()

    assert body["status"] == "open"
    assert body["awards_remaining"] == 3
    assert body["available_mrwk"] == "45"
    assert body["effective_awards_remaining"] == 0
    assert body["effective_available_mrwk"] == "0"
    assert body["pending_close_proposal"]["proposal_id"] == proposal_id
    assert body["availability_state"] == "pending_close"
    assert "pending close proposal" in body["availability_note"]


def test_bounty_api_reports_paid_multi_award_as_exhausted(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=20,
            issue_url="https://github.com/ramimbo/mergework/issues/20",
            title="Multi-award payout edge case",
            reward_mrwk="15",
            max_awards=2,
            acceptance="Each accepted submission earns one award.",
        )
        bounty_id = bounty.id
        pay_bounty(
            session,
            bounty_id=bounty_id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/20",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        pay_bounty(
            session,
            bounty_id=bounty_id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/21",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    body = client.get(f"/api/v1/bounties/{bounty_id}").json()

    assert body["status"] == "paid"
    assert body["max_awards"] == 2
    assert body["awards_paid"] == 2
    assert body["awards_remaining"] == 0
    assert body["available_mrwk"] == "0"
    assert body["reserved_mrwk"] == "30"


def test_bounty_api_reports_closed_multi_award_as_unavailable(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=21,
            issue_url="https://github.com/ramimbo/mergework/issues/21",
            title="Partial close payout edge case",
            reward_mrwk="10",
            max_awards=3,
            acceptance="Each accepted submission earns one award.",
        )
        bounty_id = bounty.id
        pay_bounty(
            session,
            bounty_id=bounty_id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/22",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        close_bounty(
            session,
            bounty_id=bounty_id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/21#close",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    body = client.get(f"/api/v1/bounties/{bounty_id}").json()

    assert body["status"] == "closed"
    assert body["max_awards"] == 3
    assert body["awards_paid"] == 1
    assert body["awards_remaining"] == 0
    assert body["available_mrwk"] == "0"
    assert body["reserved_mrwk"] == "30"


def test_bounty_api_keeps_terminal_multi_awards_visible_but_inactive(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=36,
            issue_url="https://github.com/ramimbo/mergework/issues/36",
            title="Paid multi-award API visibility",
            reward_mrwk="8",
            max_awards=2,
            acceptance="Each accepted submission earns one award.",
        )
        closed_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=37,
            issue_url="https://github.com/ramimbo/mergework/issues/37",
            title="Closed multi-award API visibility",
            reward_mrwk="6",
            max_awards=3,
            acceptance="Close releases unpaid awards.",
        )
        paid_bounty_id = paid_bounty.id
        closed_bounty_id = closed_bounty.id

        for pull_number, login in ((36, "alice"), (37, "bob")):
            pay_bounty(
                session,
                bounty_id=paid_bounty_id,
                to_account=f"github:{login}",
                submission_url=f"https://github.com/ramimbo/mergework/pull/{pull_number}",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
            )
        pay_bounty(
            session,
            bounty_id=closed_bounty_id,
            to_account="github:carol",
            submission_url="https://github.com/ramimbo/mergework/pull/38",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        close_bounty(
            session,
            bounty_id=closed_bounty_id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/37#close",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    paid_detail = client.get(f"/api/v1/bounties/{paid_bounty_id}").json()
    closed_detail = client.get(f"/api/v1/bounties/{closed_bounty_id}").json()
    listed = {bounty["id"]: bounty for bounty in client.get("/api/v1/bounties").json()}
    status = client.get("/api/v1/status").json()

    assert paid_detail["status"] == "paid"
    assert paid_detail["awards_paid"] == 2
    assert paid_detail["awards_remaining"] == 0
    assert closed_detail["status"] == "closed"
    assert closed_detail["awards_paid"] == 1
    assert closed_detail["awards_remaining"] == 0
    assert listed[paid_bounty_id]["status"] == "paid"
    assert listed[paid_bounty_id]["awards_remaining"] == 0
    assert listed[closed_bounty_id]["status"] == "closed"
    assert listed[closed_bounty_id]["awards_remaining"] == 0
    assert status["active_bounties"] == 0


def test_bounty_api_filters_by_status(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        open_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=40,
            issue_url="https://github.com/ramimbo/mergework/issues/40",
            title="Open status filter bounty",
            reward_mrwk="5",
            acceptance="Open rows should be filterable.",
        )
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=41,
            issue_url="https://github.com/ramimbo/mergework/issues/41",
            title="Paid status filter bounty",
            reward_mrwk="5",
            acceptance="Paid rows should be filterable.",
        )
        closed_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=42,
            issue_url="https://github.com/ramimbo/mergework/issues/42",
            title="Closed status filter bounty",
            reward_mrwk="5",
            acceptance="Closed rows should be filterable.",
        )
        pay_bounty(
            session,
            bounty_id=paid_bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/41",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        close_bounty(
            session,
            bounty_id=closed_bounty.id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/42#close",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    assert [item["id"] for item in client.get("/api/v1/bounties?status=open").json()] == [
        open_bounty.id
    ]
    assert [item["id"] for item in client.get("/api/v1/bounties?status=paid").json()] == [
        paid_bounty.id
    ]
    assert [item["id"] for item in client.get("/api/v1/bounties?status=closed").json()] == [
        closed_bounty.id
    ]
    assert [item["id"] for item in client.get("/api/v1/bounties?status=OPEN").json()] == [
        open_bounty.id
    ]
    assert [item["id"] for item in client.get("/api/v1/bounties?status= Paid ").json()] == [
        paid_bounty.id
    ]
    invalid = client.get("/api/v1/bounties?status=bogus")
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "status must be one of: open, paid, closed"


def test_bounty_api_search_query_rejects_control_characters(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    list_response = client.get("/api/v1/bounties?q=%C2%85open")
    summary_response = client.get("/api/v1/bounties/summary?q=open%09")

    assert list_response.status_code == 400
    assert list_response.json()["detail"] == "q must not contain control characters"
    assert summary_response.status_code == 400
    assert summary_response.json()["detail"] == "q must not contain control characters"


def test_bounty_api_search_query_rejects_oversized_values(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    accepted_query = "a" * 500
    oversized_query = "a" * 501

    list_boundary = client.get("/api/v1/bounties", params={"q": accepted_query})
    summary_boundary = client.get("/api/v1/bounties/summary", params={"q": accepted_query})
    list_oversized = client.get("/api/v1/bounties", params={"q": oversized_query})
    summary_oversized = client.get("/api/v1/bounties/summary", params={"q": oversized_query})

    assert list_boundary.status_code == 200
    assert summary_boundary.status_code == 200
    assert list_oversized.status_code == 400
    assert list_oversized.json()["detail"] == "q must be at most 500 characters"
    assert summary_oversized.status_code == 400
    assert summary_oversized.json()["detail"] == "q must be at most 500 characters"


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        ("/api/v1/bounties?limit=not-an-int&limit=1", "limit must be provided at most once"),
        ("/api/v1/bounties?sort=bogus&sort=newest", "sort must be provided at most once"),
        (
            "/api/v1/bounties/summary?repo=a%2Fb&repo=c%2Fd",
            "repo must be provided at most once",
        ),
        (
            "/api/v1/bounties/summary?issue_number=abc&issue_number=1",
            "issue_number must be provided at most once",
        ),
        ("/api/v1/bounties?status=bogus&status=open", "status must be provided at most once"),
        (
            "/api/v1/bounties/summary?q=first&q=second",
            "q must be provided at most once",
        ),
        (
            "/api/v1/bounties?availability=bad&availability=all",
            "availability must be provided at most once",
        ),
    ],
)
def test_bounty_api_rejects_repeated_scalar_filters(
    sqlite_url: str, path: str, detail: str
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(path)

    assert response.status_code == 400
    assert response.json()["detail"] == detail


def test_bounty_api_limit_caps_filtered_rows(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        first = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=50,
            issue_url="https://github.com/ramimbo/mergework/issues/50",
            title="Old open bounty",
            reward_mrwk="5",
            acceptance="Older open bounty.",
        )
        second = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=51,
            issue_url="https://github.com/ramimbo/mergework/issues/51",
            title="Middle open bounty",
            reward_mrwk="5",
            acceptance="Middle open bounty.",
        )
        third = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=52,
            issue_url="https://github.com/ramimbo/mergework/issues/52",
            title="Newest open bounty",
            reward_mrwk="5",
            acceptance="Newest open bounty.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    limited = client.get("/api/v1/bounties?status=open&limit=2")
    summary = client.get("/api/v1/bounties/summary?status=open&limit=2")

    assert [item["id"] for item in limited.json()] == [third.id, second.id]
    assert summary.json()["bounties_shown"] == 2
    assert summary.json()["open_awards"] == 2
    assert first.id not in [item["id"] for item in limited.json()]


def test_bounty_api_limit_rejects_out_of_range_values(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    assert client.get("/api/v1/bounties?limit=0").status_code == 422
    assert client.get("/api/v1/bounties?limit=201").status_code == 422
    assert client.get("/api/v1/bounties/summary?limit=0").status_code == 422
    assert client.get("/api/v1/bounties/summary?limit=201").status_code == 422

    controlled_list = client.get("/api/v1/bounties?limit=%C2%8550")
    controlled_summary = client.get("/api/v1/bounties/summary?limit=50%C2%85")
    decimal_list = client.get("/api/v1/bounties?limit=50.0")
    plus_summary = client.get("/api/v1/bounties/summary?limit=%2B50")
    leading_zero_list = client.get("/api/v1/bounties?limit=050")

    assert controlled_list.status_code == 400
    assert controlled_list.json()["detail"] == "limit must not contain control characters"
    assert controlled_summary.status_code == 400
    assert controlled_summary.json()["detail"] == "limit must not contain control characters"
    assert decimal_list.status_code == 400
    assert decimal_list.json()["detail"] == "limit must be a canonical positive integer"
    assert plus_summary.status_code == 400
    assert plus_summary.json()["detail"] == "limit must be a canonical positive integer"
    assert leading_zero_list.status_code == 400
    assert leading_zero_list.json()["detail"] == "limit must be a canonical positive integer"


def test_bounty_api_issue_number_rejects_sqlite_overflow_values(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    oversized_issue_number = SQLITE_INTEGER_MAX + 1

    bounties = client.get(f"/api/v1/bounties?issue_number={oversized_issue_number}")
    summary = client.get(f"/api/v1/bounties/summary?issue_number={oversized_issue_number}")

    assert bounties.status_code == 422
    assert summary.status_code == 422

    at_limit_bounties = client.get(f"/api/v1/bounties?issue_number={SQLITE_INTEGER_MAX}")
    at_limit_summary = client.get(f"/api/v1/bounties/summary?issue_number={SQLITE_INTEGER_MAX}")

    assert at_limit_bounties.status_code == 200
    assert at_limit_summary.status_code == 200


def test_bounty_api_filters_by_exact_repo_and_issue_number(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        mergework_649 = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=649,
            issue_url="https://github.com/ramimbo/mergework/issues/649",
            title="MergeWork proposed-work intake",
            reward_mrwk="50",
            max_awards=20,
            acceptance="Useful proposed-work issue submissions.",
        )
        other_mergework = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=650,
            issue_url="https://github.com/ramimbo/mergework/issues/650",
            title="Other MergeWork bounty",
            reward_mrwk="25",
            acceptance="Other source issue.",
        )
        other_repo_same_issue = create_bounty(
            session,
            repo="example/other",
            issue_number=649,
            issue_url="https://github.com/example/other/issues/649",
            title="Same issue number in another repo",
            reward_mrwk="10",
            acceptance="Same issue number should remain distinguishable by repo.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    by_repo = client.get("/api/v1/bounties?repo=RAMIMBO%2FMergeWork")
    exact = client.get("/api/v1/bounties?repo=ramimbo%2Fmergework&issue_number=649")
    by_issue = client.get("/api/v1/bounties?issue_number=649")
    summary = client.get("/api/v1/bounties/summary?repo=ramimbo%2Fmergework")
    composed = client.get("/api/v1/bounties?repo=ramimbo%2Fmergework&q=proposed-work")
    controlled_issue = client.get("/api/v1/bounties?issue_number=%C2%85649")
    controlled_summary_issue = client.get("/api/v1/bounties/summary?issue_number=649%C2%85")
    decimal_issue = client.get("/api/v1/bounties?issue_number=649.0")
    plus_summary_issue = client.get("/api/v1/bounties/summary?issue_number=%2B649")
    leading_zero_issue = client.get("/api/v1/bounties?issue_number=00649")

    assert [row["id"] for row in by_repo.json()] == [other_mergework.id, mergework_649.id]
    assert [row["id"] for row in exact.json()] == [mergework_649.id]
    assert [row["id"] for row in by_issue.json()] == [
        other_repo_same_issue.id,
        mergework_649.id,
    ]
    assert summary.json()["bounties_shown"] == 2
    assert summary.json()["open_awards"] == 21
    assert [row["id"] for row in composed.json()] == [mergework_649.id]

    invalid_repo = client.get("/api/v1/bounties?repo=ramimbo%C2%85mergework")
    max_length_repo = client.get("/api/v1/bounties", params={"repo": "a" * 200})
    max_length_summary_repo = client.get("/api/v1/bounties/summary", params={"repo": "a" * 200})
    oversized_repo = client.get("/api/v1/bounties", params={"repo": "a" * 201})
    oversized_summary_repo = client.get("/api/v1/bounties/summary", params={"repo": "a" * 201})
    assert invalid_repo.status_code == 400
    assert invalid_repo.json()["detail"] == "repo must not contain control characters"
    assert max_length_repo.status_code == 200
    assert max_length_summary_repo.status_code == 200
    assert oversized_repo.status_code == 400
    assert oversized_repo.json()["detail"] == "repo is too long"
    assert oversized_summary_repo.status_code == 400
    assert oversized_summary_repo.json()["detail"] == "repo is too long"
    assert controlled_issue.status_code == 400
    assert controlled_issue.json()["detail"] == "issue_number must not contain control characters"
    assert controlled_summary_issue.status_code == 400
    assert (
        controlled_summary_issue.json()["detail"]
        == "issue_number must not contain control characters"
    )
    assert decimal_issue.status_code == 400
    assert decimal_issue.json()["detail"] == "issue_number must be a canonical positive integer"
    assert plus_summary_issue.status_code == 400
    assert (
        plus_summary_issue.json()["detail"] == "issue_number must be a canonical positive integer"
    )
    assert leading_zero_issue.status_code == 400
    assert (
        leading_zero_issue.json()["detail"] == "issue_number must be a canonical positive integer"
    )
