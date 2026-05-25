from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import close_bounty, create_bounty, ensure_genesis, pay_bounty
from app.main import create_app
from app.models import Proof


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
    assert "Bounty list summary" in all_rows.text
    assert "Bounties shown" in all_rows.text
    assert "Awards open" in all_rows.text
    assert "Open reward pool" in all_rows.text
    assert "1</strong>" in all_rows.text
    assert "50 MRWK</strong>" in all_rows.text
    assert "50 MRWK still available" in all_rows.text

    paid_rows = client.get("/bounties?status=paid")
    assert paid_rows.status_code == 200
    assert "Paid public bounty" in paid_rows.text
    assert "Open public bounty" not in paid_rows.text
    assert f'href="/bounties/{paid_bounty.id}"' in paid_rows.text
    assert 'href="/bounties?status=paid"' in paid_rows.text
    assert "0 MRWK</strong>" in paid_rows.text

    paid_rows_uppercase = client.get("/bounties?status=PAID")
    assert paid_rows_uppercase.status_code == 200
    assert "Paid public bounty" in paid_rows_uppercase.text
    assert "Open public bounty" not in paid_rows_uppercase.text
    assert 'href="/bounties?status=paid" aria-current="page"' in paid_rows_uppercase.text


def test_bounties_summary_api_matches_public_list_filters(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        open_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=62,
            issue_url="https://github.com/ramimbo/mergework/issues/62",
            title="Open discovery bounty",
            reward_mrwk="25",
            max_awards=3,
            acceptance="Discovery summary should show remaining public award slots.",
        )
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=63,
            issue_url="https://github.com/ramimbo/mergework/issues/63",
            title="Docs cleanup bounty",
            reward_mrwk="40",
            acceptance="Docs cleanup.",
        )
        pay_bounty(
            session,
            bounty_id=open_bounty.id,
            to_account="github:contributor",
            submission_url="https://github.com/ramimbo/mergework/pull/62",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    summary = client.get("/api/v1/bounties/summary?q=discovery").json()
    assert summary == {
        "bounties_shown": 1,
        "open_awards": 2,
        "open_pool_mrwk": "50",
    }

    paid_summary = client.get("/api/v1/bounties/summary?status=paid&q=discovery").json()
    assert paid_summary == {
        "bounties_shown": 0,
        "open_awards": 0,
        "open_pool_mrwk": "0",
    }

    invalid = client.get("/api/v1/bounties/summary?status=bogus")
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "status must be one of: open, paid, closed"


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

    oversized_issue_search = client.get("/api/v1/bounties", params={"q": "9" * 40})
    assert oversized_issue_search.status_code == 200
    assert oversized_issue_search.json() == []

    digit_limit_issue_search = client.get("/api/v1/bounties", params={"q": "9" * 5000})
    assert digit_limit_issue_search.status_code == 200
    assert digit_limit_issue_search.json() == []

    oversized_issue_page = client.get("/bounties", params={"q": "9" * 40})
    assert oversized_issue_page.status_code == 200
    assert "No bounties yet." in oversized_issue_page.text

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
    assert "<span>Available</span>" in response.text
    assert "<span>Issue</span>" in response.text
    assert "100 MRWK" in response.text
    assert "What has to be true" in response.text
    assert "Focused PR improves status, reward, issue link, and acceptance text." in response.text

    missing_response = client.get("/api/v1/bounties/999")
    assert missing_response.status_code == 404
    assert client.get("/api/v1/bounties/0").status_code == 400
    assert client.get("/bounties/0").status_code == 400

    oversized_bounty_id = "9" * 40
    oversized_api_response = client.get(f"/api/v1/bounties/{oversized_bounty_id}")
    assert oversized_api_response.status_code == 400
    assert oversized_api_response.json()["detail"] == "bounty id is too large"
    oversized_page_response = client.get(f"/bounties/{oversized_bounty_id}")
    assert oversized_page_response.status_code == 400


def test_bounty_detail_shows_accepted_award_history(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=164,
            issue_url="https://github.com/ramimbo/mergework/issues/164",
            title="Improve bounty discovery pages",
            reward_mrwk="100",
            max_awards=3,
            acceptance="Bounty detail pages should show accepted work and proofs.",
        )
        first_proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/201",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        second_proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/202",
            accepted_by="reviewer",
            verifier_result={"label": "mrwk:accepted"},
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    api_detail = client.get(f"/api/v1/bounties/{bounty_id}").json()
    page = client.get(f"/bounties/{bounty_id}")

    assert [award["proof_hash"] for award in api_detail["accepted_awards"]] == [
        second_proof.hash,
        first_proof.hash,
    ]
    assert api_detail["accepted_awards"][0]["account"] == "github:bob"
    assert api_detail["accepted_awards"][0]["submission_url"] == (
        "https://github.com/ramimbo/mergework/pull/202"
    )
    assert page.status_code == 200
    assert "Accepted work" in page.text
    assert "2/3 awards paid" in page.text
    assert "1 still open" in page.text
    assert 'href="https://github.com/ramimbo/mergework/pull/202"' in page.text
    assert f'href="/proofs/{second_proof.hash}"' in page.text
    assert f'href="/ledger/{second_proof.ledger_sequence}"' in page.text
    assert "/accounts/github:bob" in page.text


def test_bounty_detail_skips_malformed_award_proof_payloads(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=165,
            issue_url="https://github.com/ramimbo/mergework/issues/165",
            title="Malformed proof award history",
            reward_mrwk="50",
            max_awards=2,
            acceptance="Bounty detail pages should skip malformed proof rows.",
        )
        valid_proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/203",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        malformed_proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/204",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        proof_row = session.get(Proof, malformed_proof.hash)
        assert proof_row is not None
        proof_row.public_json = "{"
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    api_detail = client.get(f"/api/v1/bounties/{bounty_id}")
    page = client.get(f"/bounties/{bounty_id}")

    assert api_detail.status_code == 200
    assert page.status_code == 200
    assert [award["proof_hash"] for award in api_detail.json()["accepted_awards"]] == [
        valid_proof.hash
    ]
    assert malformed_proof.hash not in page.text


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

    oversized_sequence = "9" * 40
    oversized_api_response = client.get(f"/api/v1/ledger/{oversized_sequence}")
    assert oversized_api_response.status_code == 400
    assert oversized_api_response.json()["detail"] == "ledger sequence is too large"
    oversized_page_response = client.get(f"/ledger/{oversized_sequence}")
    assert oversized_page_response.status_code == 400

    proof_page = client.get(f"/proofs/{proof_hash}")
    assert proof_page.status_code == 200
    assert "Bounty payment proof" in proof_page.text
    assert "Accepted bounty payment" in proof_page.text
    assert "Bounty issue" in proof_page.text
    assert "MergeWork bounty" in proof_page.text
    assert f'href="/bounties/{bounty.id}"' in proof_page.text
    assert f'href="/ledger/{payment_sequence}"' in proof_page.text

    missing_proof = client.get(f"/api/v1/proofs/{'0' * 64}")
    assert missing_proof.status_code == 404
    assert client.get("/api/v1/proofs/not-a-proof-hash").status_code == 400
    assert client.get("/proofs/not-a-proof-hash").status_code == 400


@pytest.mark.parametrize("public_json", ["{", "[]"])
def test_proof_api_reports_invalid_stored_payload(sqlite_url: str, public_json: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=24,
            issue_url="https://github.com/ramimbo/mergework/issues/24",
            title="Malformed proof payload",
            reward_mrwk="50",
            acceptance="Direct proof lookups should report invalid stored payloads.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/24",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        proof_row = session.get(Proof, proof.hash)
        assert proof_row is not None
        proof_row.public_json = public_json

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(f"/api/v1/proofs/{proof.hash}")

    assert response.status_code == 500
    assert response.json()["detail"] == "invalid proof payload"
