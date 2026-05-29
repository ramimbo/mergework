from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import add_ledger_entry, create_bounty, ensure_genesis, pay_bounty
from app.main import create_app


def test_activity_api_summarizes_proof_backed_bounty_payments(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        first_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=10,
            issue_url="https://github.com/ramimbo/mergework/issues/10",
            title="First activity bounty",
            reward_mrwk="25",
            max_awards=2,
            acceptance="Activity should count accepted bounty payments.",
        )
        second_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=11,
            issue_url="https://github.com/ramimbo/mergework/issues/11",
            title="Second activity bounty",
            reward_mrwk="40",
            acceptance="Activity should keep wallet and GitHub accounts separate.",
        )
        first_proof = pay_bounty(
            session,
            bounty_id=first_bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/10",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        second_proof = pay_bounty(
            session,
            bounty_id=first_bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/issues/10#issuecomment-1",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        wallet_proof = pay_bounty(
            session,
            bounty_id=second_bounty.id,
            to_account="mrwk1abc",
            submission_url="https://github.com/ramimbo/mergework/pull/11",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        add_ledger_entry(
            session,
            entry_type="bounty_payment",
            from_account="reserve:bounty:999",
            to_account="github:alice",
            amount_microunits=999_000000,
            reference="https://github.com/ramimbo/mergework/pull/unproved",
        )
        add_ledger_entry(
            session,
            entry_type="github_claim",
            from_account="github:alice",
            to_account="mrwk1abc",
            amount_microunits=25_000000,
            reference="claim",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/api/v1/activity")

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"] == {
        "accepted_awards": 3,
        "accepted_mrwk": "90",
        "contributors": 2,
    }
    assert payload["contributors"][0] == {
        "account": "github:alice",
        "accepted_awards": 2,
        "accepted_mrwk": "50",
        "latest_submission_url": "https://github.com/ramimbo/mergework/issues/10#issuecomment-1",
        "latest_bounty_repo": "ramimbo/mergework",
        "latest_bounty_issue_number": 10,
        "latest_bounty_issue_url": "https://github.com/ramimbo/mergework/issues/10",
        "latest_proof_hash": second_proof.hash,
        "latest_proof_url": f"/proofs/{second_proof.hash}",
    }
    assert payload["contributors"][1]["account"] == "mrwk1abc"
    assert payload["contributors"][1]["accepted_mrwk"] == "40"
    assert payload["contributors"][1]["latest_proof_hash"] == wallet_proof.hash
    assert [row["proof_hash"] for row in payload["recent"]] == [
        wallet_proof.hash,
        second_proof.hash,
        first_proof.hash,
    ]
    assert payload["recent"][0]["bounty_issue_url"] == (
        "https://github.com/ramimbo/mergework/issues/11"
    )
    assert payload["recent"][0]["bounty_repo"] == "ramimbo/mergework"
    assert payload["recent"][0]["bounty_issue_number"] == 11
    assert payload["recent"][0]["bounty_id"] == second_bounty.id
    assert payload["recent"][0]["bounty_url"] == f"/bounties/{second_bounty.id}"
    assert all("unproved" not in row["submission_url"] for row in payload["recent"])


def test_activity_api_filters_accepted_work_by_query(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=164,
            issue_url="https://github.com/ramimbo/mergework/issues/164",
            title="Activity search bounty",
            reward_mrwk="100",
            max_awards=2,
            acceptance="Activity search should find accepted work quickly.",
        )
        alice_proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/164",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/165",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    by_account = client.get("/api/v1/activity?q=ALICE").json()
    by_repo = client.get("/api/v1/activity?q=ramimbo%2Fmergework").json()
    by_proof = client.get(f"/api/v1/activity?q={alice_proof.hash[:12]}").json()
    by_issue_ref = client.get("/api/v1/activity?q=%23164").json()
    no_match = client.get("/api/v1/activity?q=carol").json()
    invalid_hash_queries = [
        client.get("/api/v1/activity", params={"q": query}).json()
        for query in ("#", "#abc", "#123abc")
    ]

    assert by_account["query"] == "alice"
    assert by_account["totals"] == {
        "accepted_awards": 1,
        "accepted_mrwk": "100",
        "contributors": 1,
    }
    assert by_account["contributors"][0]["account"] == "github:alice"
    assert by_account["recent"][0]["submission_url"].endswith("/pull/164")
    assert by_issue_ref["query"] == "#164"
    assert by_issue_ref["totals"] == {
        "accepted_awards": 2,
        "accepted_mrwk": "200",
        "contributors": 2,
    }
    assert {row["bounty_issue_number"] for row in by_issue_ref["recent"]} == {164}
    assert by_repo["totals"] == {
        "accepted_awards": 2,
        "accepted_mrwk": "200",
        "contributors": 2,
    }
    assert by_proof["recent"][0]["proof_hash"] == alice_proof.hash
    assert no_match["totals"] == {
        "accepted_awards": 0,
        "accepted_mrwk": "0",
        "contributors": 0,
    }
    assert no_match["contributors"] == []
    assert no_match["recent"] == []
    for invalid_hash_query in invalid_hash_queries:
        assert invalid_hash_query["totals"] == {
            "accepted_awards": 0,
            "accepted_mrwk": "0",
            "contributors": 0,
        }
        assert invalid_hash_query["contributors"] == []
        assert invalid_hash_query["recent"] == []


def test_activity_page_renders_empty_and_paid_states(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    empty = client.get("/activity")

    assert empty.status_code == 200
    assert "Accepted work activity" in empty.text
    assert "No accepted bounty payments yet." in empty.text

    with session_scope(sqlite_url) as session:
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=12,
            issue_url="https://github.com/ramimbo/mergework/issues/12",
            title="Activity page bounty",
            reward_mrwk="75",
            acceptance="Activity page should link accepted work proofs.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/12",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    paid = client.get("/activity")

    assert paid.status_code == 200
    assert "github:bob" in paid.text
    assert "75 MRWK" in paid.text
    assert 'role="search"' in paid.text
    assert 'aria-label="Activity inspection links"' in paid.text
    assert 'href="/api/v1/activity">View JSON activity</a>' in paid.text
    assert 'name="q"' in paid.text
    assert f'href="/bounties/{bounty.id}">Bounty #{bounty.id}</a>' in paid.text
    assert "Latest bounty" in paid.text
    assert 'href="https://github.com/ramimbo/mergework/issues/12"' in paid.text
    assert 'href="https://github.com/ramimbo/mergework/pull/12"' in paid.text
    assert f'href="/proofs/{proof.hash}"' in paid.text
    assert "/accounts/github:bob" in paid.text

    filtered = client.get("/activity?q=bob")
    issue_ref = client.get("/activity?q=%2312")

    assert filtered.status_code == 200
    assert 'value="bob"' in filtered.text
    assert "Showing accepted work matching “bob”." in filtered.text
    assert 'href="/api/v1/activity?q=bob">View JSON activity</a>' in filtered.text
    assert 'href="/activity">Clear</a>' in filtered.text
    assert "No contributors match this search." not in filtered.text
    assert "No accepted work matches this search." not in filtered.text
    assert issue_ref.status_code == 200
    assert 'value="#12"' in issue_ref.text
    assert "Showing accepted work matching “#12”." in issue_ref.text
    assert 'href="/api/v1/activity?q=%2312">View JSON activity</a>' in issue_ref.text
    assert "github:bob" in issue_ref.text

    no_match = client.get("/activity?q=alice")

    assert no_match.status_code == 200
    assert 'value="alice"' in no_match.text
    assert "Showing accepted work matching “alice”." in no_match.text
    assert "No contributors match this search." in no_match.text
    assert "No accepted work matches this search." in no_match.text
    assert "No accepted bounty payments yet." not in no_match.text
    assert "No proof-backed accepted work rows yet." not in no_match.text
    assert 'href="/activity">Clear search</a>' in no_match.text
