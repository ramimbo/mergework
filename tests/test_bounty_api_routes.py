from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import close_bounty, create_bounty, ensure_genesis, pay_bounty
from app.main import create_app


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
    shifted = client.get("/api/v1/bounties?status=open&limit=1&offset=1")
    shifted_summary = client.get("/api/v1/bounties/summary?status=open&limit=1&offset=1")
    exhausted = client.get("/api/v1/bounties?status=open&limit=2&offset=99")

    assert [item["id"] for item in limited.json()] == [third.id, second.id]
    assert [item["id"] for item in shifted.json()] == [second.id]
    assert summary.json()["bounties_shown"] == 2
    assert summary.json()["open_awards"] == 2
    assert shifted_summary.json()["bounties_shown"] == 1
    assert shifted_summary.json()["open_awards"] == 1
    assert first.id not in [item["id"] for item in limited.json()]
    assert exhausted.json() == []


def test_bounty_api_limit_rejects_out_of_range_values(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    assert client.get("/api/v1/bounties?limit=0").status_code == 422
    assert client.get("/api/v1/bounties?limit=201").status_code == 422
    assert client.get("/api/v1/bounties?offset=-1").status_code == 422
    assert client.get("/api/v1/bounties/summary?limit=0").status_code == 422
    assert client.get("/api/v1/bounties/summary?limit=201").status_code == 422
    assert client.get("/api/v1/bounties/summary?offset=-1").status_code == 422
