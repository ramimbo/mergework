from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import close_bounty, create_bounty, ensure_genesis, pay_bounty
from app.main import create_app


def test_bounties_page_renders_and_filters_by_status(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        open_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=50,
            issue_url="https://github.com/ramimbo/mergework/issues/50",
            title="Open public bounty",
            reward_mrwk="50",
            acceptance="Open bounty should appear on the public list.",
        )
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=51,
            issue_url="https://github.com/ramimbo/mergework/issues/51",
            title="Paid public bounty",
            reward_mrwk="50",
            acceptance="Paid bounty should appear when filtering paid rows.",
        )
        pay_bounty(
            session,
            bounty_id=paid_bounty.id,
            to_account="github:contributor",
            submission_url="https://github.com/ramimbo/mergework/pull/51",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    all_rows = client.get("/bounties")
    assert all_rows.status_code == 200
    assert "Open public bounty" in all_rows.text
    assert "Paid public bounty" in all_rows.text
    assert f'href="/bounties/{open_bounty.id}"' in all_rows.text
    assert (
        'href="https://github.com/ramimbo/mergework/issues/50" rel="nofollow noopener"'
        in all_rows.text
    )
    assert "ramimbo/mergework #50" in all_rows.text

    paid_rows = client.get("/bounties?status=paid")
    assert paid_rows.status_code == 200
    assert "Paid public bounty" in paid_rows.text
    assert "Open public bounty" not in paid_rows.text
    assert f'href="/bounties/{paid_bounty.id}"' in paid_rows.text
    assert 'href="/bounties?status=paid"' in paid_rows.text

    paid_rows_uppercase = client.get("/bounties?status=PAID")
    assert paid_rows_uppercase.status_code == 200
    assert "Paid public bounty" in paid_rows_uppercase.text
    assert "Open public bounty" not in paid_rows_uppercase.text
    assert 'href="/bounties?status=paid" aria-current="page"' in paid_rows_uppercase.text


def test_bounties_page_and_api_search_by_text_and_issue_number(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=64,
            issue_url="https://github.com/ramimbo/mergework/issues/64",
            title="Improve public bounty discovery",
            reward_mrwk="100",
            acceptance="Make contributor search find award slots and proof inspection work.",
        )
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=65,
            issue_url="https://github.com/ramimbo/mergework/issues/65",
            title="Internal admin cleanup",
            reward_mrwk="100",
            acceptance="Private admin-only cleanup.",
        )
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=66,
            issue_url="https://github.com/ramimbo/mergework/issues/66",
            title="Literal 100% release_note path",
            reward_mrwk="100",
            acceptance=r"Document C:\work\mergework examples.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    text_search = client.get("/bounties?q=proof+inspection")
    assert text_search.status_code == 200
    assert "Search bounties" in text_search.text
    assert "Showing matches for “proof inspection”." in text_search.text
    assert "Improve public bounty discovery" in text_search.text
    assert "Internal admin cleanup" not in text_search.text
    assert 'href="/bounties?status=open&q=proof%20inspection"' in text_search.text

    issue_search = client.get("/api/v1/bounties?q=65")
    assert issue_search.status_code == 200
    assert [row["issue_number"] for row in issue_search.json()] == [65]

    percent_search = client.get("/api/v1/bounties?q=%25")
    assert percent_search.status_code == 200
    assert [row["issue_number"] for row in percent_search.json()] == [66]

    underscore_search = client.get("/api/v1/bounties?q=_")
    assert underscore_search.status_code == 200
    assert [row["issue_number"] for row in underscore_search.json()] == [66]

    backslash_search = client.get("/api/v1/bounties", params={"q": "\\"})
    assert backslash_search.status_code == 200
    assert [row["issue_number"] for row in backslash_search.json()] == [66]


def test_bounty_detail_highlights_action_fields(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=4,
            issue_url="https://github.com/ramimbo/mergework/issues/4",
            title="Improve bounty detail page clarity",
            reward_mrwk="100",
            acceptance="Focused PR improves status, reward, issue link, and acceptance text.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(f"/bounties/{bounty.id}")

    assert response.status_code == 200
    assert "Bounty summary" in response.text
    assert "<span>Status</span>" in response.text
    assert "<span>Reward per award</span>" in response.text
    assert "<span>Awards</span>" in response.text
    assert "<span>Issue</span>" in response.text
    assert "100 MRWK" in response.text
    assert "Ready for contributors" in response.text
    assert "open for 1 more award" in response.text
    assert "Bounty #4" in response.text
    assert "What has to be true" in response.text
    assert "Focused PR improves status, reward, issue link, and acceptance text." in response.text

    missing_response = client.get("/api/v1/bounties/999")
    assert missing_response.status_code == 404
    assert client.get("/api/v1/bounties/0").status_code == 400
    assert client.get("/bounties/0").status_code == 400


def test_bounty_detail_explains_paid_and_closed_action_states(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=14,
            issue_url="https://github.com/ramimbo/mergework/issues/14",
            title="Paid discovery bounty",
            reward_mrwk="50",
            max_awards=1,
            acceptance="Accepted work should make the paid state obvious.",
        )
        pay_bounty(
            session,
            bounty_id=paid_bounty.id,
            to_account="github:contributor",
            submission_url="https://github.com/ramimbo/mergework/pull/14",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        closed_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=15,
            issue_url="https://github.com/ramimbo/mergework/issues/15",
            title="Closed discovery bounty",
            reward_mrwk="50",
            acceptance="Closed work should not look claimable.",
        )
        close_bounty(
            session,
            bounty_id=closed_bounty.id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/15",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    paid_response = client.get(f"/bounties/{paid_bounty.id}")
    assert paid_response.status_code == 200
    assert "All awards paid" in paid_response.text
    assert "used its award slots" in paid_response.text
    assert "Ready for contributors" not in paid_response.text

    closed_response = client.get(f"/bounties/{closed_bounty.id}")
    assert closed_response.status_code == 200
    assert "Closed for new work" in closed_response.text
    assert "look for an open bounty" in closed_response.text
    assert "Ready for contributors" not in closed_response.text


def test_ledger_and_proof_pages_make_bounty_payments_scannable(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=23,
            issue_url="https://github.com/ramimbo/mergework/issues/23",
            title="Improve ledger bounty payment scanning",
            reward_mrwk="150",
            max_awards=2,
            acceptance="Ledger and proof explorers clearly identify bounty payment entries.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:contributor",
            submission_url="https://github.com/ramimbo/mergework/pull/99",
            accepted_by="maintainer",
            verifier_result={"result": "accepted"},
        )
        close_bounty(
            session,
            bounty_id=bounty.id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/23",
        )
        proof_hash = proof.hash
        payment_sequence = proof.ledger_sequence

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    ledger_page = client.get("/ledger")
    assert ledger_page.status_code == 200
    assert "Bounty Reserve" in ledger_page.text
    assert "Bounty Payment" in ledger_page.text
    assert "Bounty Release" in ledger_page.text
    assert "Funds reserved" in ledger_page.text
    assert "Award paid" in ledger_page.text
    assert "Unused reserve released" in ledger_page.text
    assert 'class="ledger-row ledger-row--bounty-payment"' in ledger_page.text
    assert 'href="https://github.com/ramimbo/mergework/pull/99"' in ledger_page.text
    assert f'href="/proofs/{proof_hash}">Payment proof</a>' in ledger_page.text

    ledger_entry_page = client.get(f"/ledger/{payment_sequence}")
    assert ledger_entry_page.status_code == 200
    assert "Bounty Payment" in ledger_entry_page.text
    assert "Bounty scan status" in ledger_entry_page.text
    assert "Award paid" in ledger_entry_page.text
    assert client.get("/api/v1/ledger/0").status_code == 400
    assert client.get("/ledger/0").status_code == 400

    proof_page = client.get(f"/proofs/{proof_hash}")
    assert proof_page.status_code == 200
    assert "Bounty payment proof" in proof_page.text
    assert "Accepted bounty payment" in proof_page.text
    assert "Bounty issue" in proof_page.text
    assert f'href="/ledger/{payment_sequence}"' in proof_page.text
