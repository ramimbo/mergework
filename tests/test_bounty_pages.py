from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import close_bounty, create_bounty, ensure_genesis, pay_bounty
from app.main import create_app
from app.models import LedgerEntry, Proof
from app.path_params import SQLITE_INTEGER_MAX
from app.treasury import propose_treasury_action


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
    assert f'aria-label="Bounty action links for {open_bounty.id}"' in all_rows.text
    assert f'href="/api/v1/bounties/{open_bounty.id}">JSON details</a>' in all_rows.text
    assert f'href="/api/v1/bounties/{open_bounty.id}/attempts">Active attempts</a>' in all_rows.text
    assert (
        'href="https://github.com/ramimbo/mergework/issues/50" rel="nofollow noopener"'
        in all_rows.text
    )
    assert "ramimbo/mergework #50" in all_rows.text
    assert "Bounty list summary" in all_rows.text
    assert "Bounties shown" in all_rows.text
    assert "Awards open" in all_rows.text
    assert "Open reward pool" in all_rows.text
    assert 'href="/api/v1/bounties">View JSON results</a>' in all_rows.text
    assert "1</strong>" in all_rows.text
    assert "50 MRWK</strong>" in all_rows.text
    assert "50 MRWK still available" in all_rows.text

    paid_rows = client.get("/bounties?status=paid")
    assert paid_rows.status_code == 200
    assert "Paid public bounty" in paid_rows.text
    assert "Open public bounty" not in paid_rows.text
    assert f'href="/bounties/{paid_bounty.id}"' in paid_rows.text
    assert 'href="/bounties?status=paid"' in paid_rows.text
    assert 'href="/api/v1/bounties?status=paid">View JSON results</a>' in paid_rows.text
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
        "effective_open_awards": 2,
        "effective_open_pool_mrwk": "50",
        "availability_state_counts": {"open": 1},
        "pending_payout_awards": 0,
        "reduced_capacity_bounties": 0,
        "effectively_unavailable_bounties": 0,
    }

    paid_summary = client.get("/api/v1/bounties/summary?status=paid&q=discovery").json()
    assert paid_summary == {
        "bounties_shown": 0,
        "open_awards": 0,
        "open_pool_mrwk": "0",
        "effective_open_awards": 0,
        "effective_open_pool_mrwk": "0",
        "availability_state_counts": {},
        "pending_payout_awards": 0,
        "reduced_capacity_bounties": 0,
        "effectively_unavailable_bounties": 0,
    }

    invalid = client.get("/api/v1/bounties/summary?status=bogus")
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "status must be one of: open, paid, closed"


def test_bounties_page_shows_effective_capacity_after_pending_payout(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=67,
            issue_url="https://github.com/ramimbo/mergework/issues/67",
            title="Pending payout public capacity",
            reward_mrwk="25",
            max_awards=2,
            acceptance="Public pages should show effective capacity after pending payouts.",
        )
        bounty_id = bounty.id
        proposal = propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": bounty_id,
                "to_account": "github:alice",
                "submission_url": "https://github.com/ramimbo/mergework/pull/67",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )
        proposal_id = proposal.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    page = client.get("/bounties")
    detail = client.get(f"/bounties/{bounty_id}")

    assert page.status_code == 200
    assert "Effectively open" in page.text
    assert "Effectively available" in page.text
    assert "Capacity reduced" in page.text
    assert "Effectively unavailable" in page.text
    assert re.search(r"Capacity reduced</span>\s*<strong>1</strong>", page.text)
    assert re.search(r"Effectively unavailable</span>\s*<strong>0</strong>", page.text)
    assert "50 MRWK still available before pending proposals" in page.text
    assert "25 MRWK effectively available" in page.text
    assert "1 award covered by pending payout proposal" in page.text
    assert detail.status_code == 200
    assert "<span>Effective remaining</span>" in detail.text
    assert "<span>Effective available</span>" in detail.text
    assert "Visible capacity before pending proposals: 2 awards, 50 MRWK." in detail.text
    assert "Pending treasury proposals" in detail.text
    assert "Capacity already in review" in detail.text
    assert f'href="/api/v1/treasury/proposals/{proposal_id}">Proposal #{proposal_id}</a>' in (
        detail.text
    )
    assert "would pay github:alice for" in detail.text
    assert (
        'href="https://github.com/ramimbo/mergework/pull/67" rel="nofollow noopener">'
        "submitted work</a>"
    ) in detail.text
    assert "1 award still open for distinct accepted work after pending treasury proposals." in (
        detail.text
    )


def test_bounties_page_shows_effective_capacity_after_pending_close(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=68,
            issue_url="https://github.com/ramimbo/mergework/issues/68",
            title="Pending close public capacity",
            reward_mrwk="30",
            max_awards=3,
            acceptance="Public pages should show no effective capacity after pending close.",
        )
        bounty_id = bounty.id
        proposal = propose_treasury_action(
            session,
            action="close_bounty",
            payload={
                "bounty_id": bounty_id,
                "closed_by": "maintainer",
                "reference": "https://github.com/ramimbo/mergework/issues/68#close",
            },
            proposed_by="maintainer",
        )
        proposal_id = proposal.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    page = client.get("/bounties")
    detail = client.get(f"/bounties/{bounty_id}")

    assert page.status_code == 200
    assert re.search(r"Capacity reduced</span>\s*<strong>1</strong>", page.text)
    assert re.search(r"Effectively unavailable</span>\s*<strong>1</strong>", page.text)
    assert "90 MRWK still available before pending proposals" in page.text
    assert "0 MRWK effectively available" in page.text
    assert "A pending close proposal would make this bounty unavailable if executed." in page.text
    assert detail.status_code == 200
    assert "Visible capacity before pending proposals: 3 awards, 90 MRWK." in detail.text
    assert "Pending treasury proposals" in detail.text
    assert f'href="/api/v1/treasury/proposals/{proposal_id}">Proposal #{proposal_id}</a>' in (
        detail.text
    )
    assert "would close this bounty after" in detail.text
    assert (
        'Reference: <a href="https://github.com/ramimbo/mergework/issues/68#close" '
        'rel="nofollow noopener">ramimbo/mergework/issues/68#close</a>.'
    ) in detail.text
    assert (
        "No awards remain after pending treasury proposals; treat new work as unpaid unless "
        "maintainers reopen or free capacity."
    ) in detail.text


def test_bounties_page_honors_limit_filter(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=72,
            issue_url="https://github.com/ramimbo/mergework/issues/72",
            title="Old public bounty",
            reward_mrwk="20",
            acceptance="Old row should be hidden when the page limit is two.",
        )
        middle = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=73,
            issue_url="https://github.com/ramimbo/mergework/issues/73",
            title="Middle public bounty",
            reward_mrwk="30",
            acceptance="Middle row should stay visible with the newest row.",
        )
        newest = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=74,
            issue_url="https://github.com/ramimbo/mergework/issues/74",
            title="Newest public bounty",
            reward_mrwk="40",
            acceptance="Newest row should stay visible.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    limited_page = client.get("/bounties?limit=2")
    assert limited_page.status_code == 200
    assert limited_page.text.index(newest.title) < limited_page.text.index(middle.title)
    assert "Old public bounty" not in limited_page.text
    assert "<strong>2</strong>" in limited_page.text
    assert '<option value="2" selected>2</option>' in limited_page.text
    assert '<option value=""' in limited_page.text
    assert 'href="/bounties?status=open&limit=2"' in limited_page.text

    filtered_limited_page = client.get("/bounties?q=public&sort=reward&limit=2")
    assert filtered_limited_page.status_code == 200
    assert (
        '<option value="reward" selected>Highest per-award reward</option>'
        in filtered_limited_page.text
    )
    assert 'href="/bounties?sort=reward&limit=2">Clear search</a>' in filtered_limited_page.text
    assert (
        'href="/api/v1/bounties?q=public&amp;sort=reward&amp;limit=2">View JSON results</a>'
        in filtered_limited_page.text
    )

    invalid_limit = client.get("/bounties?limit=0")
    assert invalid_limit.status_code == 422

    max_limit = client.get("/bounties?limit=200")
    assert max_limit.status_code == 200

    too_large_limit = client.get("/bounties?limit=201")
    assert too_large_limit.status_code == 422

    controlled_limit = client.get("/bounties?limit=%C2%8550")
    assert controlled_limit.status_code == 400
    assert controlled_limit.json()["detail"] == "limit must not contain control characters"


def test_bounties_page_rejects_repeated_scalar_filters(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    cases = {
        "/bounties?limit=not-an-int&limit=10": "limit must be provided at most once",
        "/bounties?issue_number=bad&issue_number=64": "issue_number must be provided at most once",
        "/bounties?status=bogus&status=open": "status must be provided at most once",
        "/bounties?q=first&q=second": "q must be provided at most once",
        "/bounties?sort=reward&sort=newest": "sort must be provided at most once",
        "/bounties?repo=ramimbo%2Fmergework&repo=example%2Fother": (
            "repo must be provided at most once"
        ),
        "/bounties?availability=all&availability=effectively_open": (
            "availability must be provided at most once"
        ),
    }

    for path, detail in cases.items():
        response = client.get(path)

        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_bounties_page_rejects_sqlite_overflow_issue_number(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    max_issue_number = client.get(f"/bounties?issue_number={SQLITE_INTEGER_MAX}")
    oversized_issue_number = client.get(f"/bounties?issue_number={SQLITE_INTEGER_MAX + 1}")

    assert max_issue_number.status_code == 200
    assert oversized_issue_number.status_code == 422


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
        create_bounty(
            session,
            repo="example/other",
            issue_number=64,
            issue_url="https://github.com/example/other/issues/64",
            title="Other repo same issue",
            reward_mrwk="25",
            acceptance="Other repository bounty.",
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

    hash_issue_search = client.get("/api/v1/bounties?q=%2365")
    assert hash_issue_search.status_code == 200
    assert [row["issue_number"] for row in hash_issue_search.json()] == [65]

    hash_issue_page = client.get("/bounties?q=%2365")
    assert hash_issue_page.status_code == 200
    assert "Showing matches for “#65”." in hash_issue_page.text
    assert "Internal admin cleanup" in hash_issue_page.text
    assert "Improve public bounty discovery" not in hash_issue_page.text

    oversized_issue_search = client.get("/api/v1/bounties", params={"q": "9" * 40})
    assert oversized_issue_search.status_code == 200
    assert oversized_issue_search.json() == []

    digit_limit_issue_search = client.get("/api/v1/bounties", params={"q": "9" * 500})
    assert digit_limit_issue_search.status_code == 200
    assert digit_limit_issue_search.json() == []

    digit_limit_issue_page = client.get("/bounties", params={"q": "9" * 500})
    assert digit_limit_issue_page.status_code == 200
    assert "No bounties match these filters." in digit_limit_issue_page.text
    assert 'href="/bounties">Clear filters</a>' in digit_limit_issue_page.text

    oversized_query = "9" * 501
    oversized_query_search = client.get("/api/v1/bounties", params={"q": oversized_query})
    oversized_query_summary = client.get("/api/v1/bounties/summary", params={"q": oversized_query})
    oversized_query_page = client.get("/bounties", params={"q": oversized_query})

    assert oversized_query_search.status_code == 400
    assert oversized_query_search.json()["detail"] == "q must be at most 500 characters"
    assert oversized_query_summary.status_code == 400
    assert oversized_query_summary.json()["detail"] == "q must be at most 500 characters"
    assert oversized_query_page.status_code == 400
    assert oversized_query_page.json()["detail"] == "q must be at most 500 characters"

    oversized_issue_page = client.get("/bounties", params={"q": "9" * 40})
    assert oversized_issue_page.status_code == 200
    assert "No bounties match these filters." in oversized_issue_page.text
    assert 'href="/bounties">Clear filters</a>' in oversized_issue_page.text

    empty_status_page = client.get("/bounties?status=paid&q=proof")
    assert empty_status_page.status_code == 200
    assert "No bounties match these filters." in empty_status_page.text
    assert 'href="/bounties">Clear filters</a>' in empty_status_page.text

    percent_search = client.get("/api/v1/bounties?q=%25")
    assert percent_search.status_code == 200
    assert [row["issue_number"] for row in percent_search.json()] == [66]

    underscore_search = client.get("/api/v1/bounties?q=_")
    assert underscore_search.status_code == 200
    assert [row["issue_number"] for row in underscore_search.json()] == [66]

    source_filter_page = client.get("/bounties?repo=ramimbo%2Fmergework&issue_number=64")
    controlled_source_filter_page = client.get(
        "/bounties?repo=ramimbo%2Fmergework&issue_number=%C2%8564"
    )
    decimal_source_filter_page = client.get("/bounties?repo=ramimbo%2Fmergework&issue_number=64.0")
    leading_zero_source_filter_page = client.get(
        "/bounties?repo=ramimbo%2Fmergework&issue_number=0064"
    )
    max_length_repo_filter_page = client.get("/bounties", params={"repo": "a" * 200})
    oversized_repo_filter_page = client.get("/bounties", params={"repo": "a" * 201})
    plus_limit_page = client.get("/bounties?limit=%2B1")
    leading_zero_limit_page = client.get("/bounties?limit=01")
    assert source_filter_page.status_code == 200
    assert "Source filter: ramimbo/mergework #64." in source_filter_page.text
    assert 'href="/bounties">Clear source filter</a>' in source_filter_page.text
    assert "Improve public bounty discovery" in source_filter_page.text
    assert "Other repo same issue" not in source_filter_page.text
    assert (
        'href="/bounties?status=open&repo=ramimbo%2Fmergework&issue_number=64"'
        in source_filter_page.text
    )
    assert (
        'href="/bounties?status=paid&repo=ramimbo%2Fmergework&issue_number=64"'
        in source_filter_page.text
    )
    assert 'href="/bounties?repo=ramimbo%2Fmergework&issue_number=64"' in source_filter_page.text
    assert (
        'href="/api/v1/bounties?repo=ramimbo%2Fmergework&amp;issue_number=64">View JSON results</a>'
    ) in source_filter_page.text

    backslash_search = client.get("/api/v1/bounties", params={"q": "\\"})
    assert backslash_search.status_code == 200
    assert [row["issue_number"] for row in backslash_search.json()] == [66]
    assert controlled_source_filter_page.status_code == 400
    assert (
        controlled_source_filter_page.json()["detail"]
        == "issue_number must not contain control characters"
    )
    assert decimal_source_filter_page.status_code == 400
    assert (
        decimal_source_filter_page.json()["detail"]
        == "issue_number must be a canonical positive integer"
    )
    assert leading_zero_source_filter_page.status_code == 400
    assert (
        leading_zero_source_filter_page.json()["detail"]
        == "issue_number must be a canonical positive integer"
    )
    assert max_length_repo_filter_page.status_code == 200
    assert oversized_repo_filter_page.status_code == 400
    assert oversized_repo_filter_page.json()["detail"] == "repo is too long"
    assert plus_limit_page.status_code == 400
    assert plus_limit_page.json()["detail"] == "limit must be a canonical positive integer"
    assert leading_zero_limit_page.status_code == 400
    assert leading_zero_limit_page.json()["detail"] == "limit must be a canonical positive integer"


def test_bounties_page_and_api_sort_public_rows(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        most_awards = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=69,
            issue_url="https://github.com/ramimbo/mergework/issues/69",
            title="Eight slot bounty",
            reward_mrwk="10",
            max_awards=8,
            acceptance="Most remaining award slots should sort first by awards.",
        )
        high_reward = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=70,
            issue_url="https://github.com/ramimbo/mergework/issues/70",
            title="High reward single slot",
            reward_mrwk="90",
            acceptance="Large per-award payout should sort first by reward.",
        )
        high_capacity = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=71,
            issue_url="https://github.com/ramimbo/mergework/issues/71",
            title="Many smaller award slots",
            reward_mrwk="25",
            max_awards=5,
            acceptance="More remaining capacity should sort first by available pool.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    default_rows = client.get("/api/v1/bounties")
    assert default_rows.status_code == 200
    assert [row["issue_number"] for row in default_rows.json()] == [71, 70, 69]

    whitespace_sort_rows = client.get("/api/v1/bounties", params={"sort": "   "})
    assert whitespace_sort_rows.status_code == 200
    assert [row["issue_number"] for row in whitespace_sort_rows.json()] == [71, 70, 69]

    reward_rows = client.get("/api/v1/bounties?sort=reward")
    assert reward_rows.status_code == 200
    assert [row["issue_number"] for row in reward_rows.json()] == [70, 71, 69]

    awards_rows = client.get("/api/v1/bounties?sort=awards")
    assert awards_rows.status_code == 200
    assert [row["issue_number"] for row in awards_rows.json()] == [69, 71, 70]

    available_page = client.get("/bounties?sort=available")
    assert available_page.status_code == 200
    assert available_page.text.index(high_capacity.title) < available_page.text.index(
        high_reward.title
    )
    assert available_page.text.index(high_reward.title) < available_page.text.index(
        most_awards.title
    )
    assert 'name="sort"' in available_page.text
    assert '<option value="available" selected>Most MRWK available</option>' in available_page.text
    assert 'href="/bounties?status=open&sort=available"' in available_page.text

    whitespace_sort_page = client.get("/bounties", params={"sort": "   "})
    assert whitespace_sort_page.status_code == 200
    assert whitespace_sort_page.text.index(high_capacity.title) < whitespace_sort_page.text.index(
        high_reward.title
    )
    assert whitespace_sort_page.text.index(high_reward.title) < whitespace_sort_page.text.index(
        most_awards.title
    )
    assert '<option value="newest" selected>Newest first</option>' in whitespace_sort_page.text

    invalid_sort = client.get("/api/v1/bounties?sort=bogus")
    assert invalid_sort.status_code == 400
    assert invalid_sort.json()["detail"] == "sort must be one of: newest, reward, available, awards"


def test_bounties_page_api_and_summary_filter_effectively_open_rows(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        effectively_open = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=80,
            issue_url="https://github.com/ramimbo/mergework/issues/80",
            title="Fresh open bounty",
            reward_mrwk="100",
            acceptance="This bounty has no pending payout proposals.",
        )
        partially_open = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=81,
            issue_url="https://github.com/ramimbo/mergework/issues/81",
            title="Partially open bounty",
            reward_mrwk="50",
            max_awards=2,
            acceptance="This bounty still has one effective award after a pending payout.",
        )
        effectively_full = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=82,
            issue_url="https://github.com/ramimbo/mergework/issues/82",
            title="Effectively full bounty",
            reward_mrwk="75",
            acceptance="This bounty is raw-open but covered by a pending payout.",
        )
        propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": partially_open.id,
                "to_account": "github:alice",
                "submission_url": "https://github.com/ramimbo/mergework/pull/81",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )
        propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": effectively_full.id,
                "to_account": "github:bob",
                "submission_url": "https://github.com/ramimbo/mergework/pull/82",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    default_rows = client.get("/api/v1/bounties?status=open")
    assert default_rows.status_code == 200
    assert [row["issue_number"] for row in default_rows.json()] == [82, 81, 80]

    filtered_rows = client.get("/api/v1/bounties?status=open&availability=effectively_open")
    assert filtered_rows.status_code == 200
    assert [row["issue_number"] for row in filtered_rows.json()] == [81, 80]

    summary = client.get("/api/v1/bounties/summary?status=open&availability=effectively_open")
    assert summary.status_code == 200
    assert summary.json()["bounties_shown"] == 2
    assert summary.json()["open_awards"] == 3
    assert summary.json()["effective_open_awards"] == 2

    page = client.get("/bounties?status=open&availability=effectively_open")
    assert page.status_code == 200
    assert effectively_open.title in page.text
    assert partially_open.title in page.text
    assert effectively_full.title not in page.text
    assert 'href="/api/v1/bounties?status=open&amp;availability=effectively_open">' in page.text

    filtered_search_page = client.get("/bounties?status=open&q=Fresh&availability=effectively_open")
    assert filtered_search_page.status_code == 200
    assert (
        'href="/bounties?status=open&availability=effectively_open">Clear search</a>'
        in filtered_search_page.text
    )
    assert 'href="/bounties?q=Fresh&availability=effectively_open"' in filtered_search_page.text
    assert (
        'href="/bounties?status=paid&q=Fresh&availability=effectively_open"'
        in filtered_search_page.text
    )

    empty_effective_page = client.get("/bounties?availability=effectively_open&q=missing")
    assert empty_effective_page.status_code == 200
    assert "No bounties match these filters." in empty_effective_page.text


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
    assert "Contributor next steps" in response.text
    assert "Before you start" in response.text
    assert "Submission requirements" in response.text
    assert "<dt>Claim command</dt>" in response.text
    assert "<code>/claim</code>" in response.text
    assert "<dt>Reference formats</dt>" in response.text
    assert "<code>Bounty #4</code>" in response.text
    assert "<code>Refs #4</code>" in response.text
    assert "<dt>Expected artifact</dt>" in response.text
    assert "focused PR, issue, report, or evidence URL" in response.text
    assert "Confirm the source issue is still open" in response.text
    assert bounty.id != bounty.issue_number
    assert "link the source issue as <strong>Bounty #4</strong>" in response.text
    assert (
        "attempts are advisory only and do not reserve MRWK, acceptance, or payment"
        in response.text
    )
    assert "1 award still open for distinct accepted work." in response.text
    assert (
        'href="https://github.com/ramimbo/mergework/issues/4" rel="nofollow noopener"'
        in response.text
    )
    assert "View source bounty matches" in response.text
    assert 'href="/bounties?repo=ramimbo/mergework&amp;issue_number=4"' in response.text
    assert f'href="/api/v1/bounties/{bounty.id}"' in response.text
    assert f'href="/api/v1/bounties/{bounty.id}/attempts"' in response.text

    missing_response = client.get("/api/v1/bounties/999")
    assert missing_response.status_code == 404
    assert client.get("/api/v1/bounties/0").status_code == 400
    assert client.get("/bounties/0").status_code == 400
    for noncanonical_id in (f"{bounty.id}.0", f"+{bounty.id}", f"%C2%85{bounty.id}"):
        api_response = client.get(f"/api/v1/bounties/{noncanonical_id}")
        assert api_response.status_code == 400
        assert api_response.json()["detail"] == "bounty id must be a positive integer"
        page_response = client.get(f"/bounties/{noncanonical_id}")
        assert page_response.status_code == 400
    leading_zero_bounty_id = f"0{bounty.id}"
    leading_zero_api_response = client.get(f"/api/v1/bounties/{leading_zero_bounty_id}")
    assert leading_zero_api_response.status_code == 400
    assert (
        leading_zero_api_response.json()["detail"]
        == "bounty id must be a canonical positive integer"
    )
    leading_zero_page_response = client.get(f"/bounties/{leading_zero_bounty_id}")
    assert leading_zero_page_response.status_code == 400

    oversized_bounty_id = "9" * 40
    oversized_api_response = client.get(f"/api/v1/bounties/{oversized_bounty_id}")
    assert oversized_api_response.status_code == 400
    assert oversized_api_response.json()["detail"] == "bounty id is too large"
    oversized_page_response = client.get(f"/bounties/{oversized_bounty_id}")
    assert oversized_page_response.status_code == 400


def test_bounty_detail_shows_issue_submission_requirements(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=649,
            issue_url="https://github.com/ramimbo/mergework/issues/649",
            title="Queue proposed-work requests",
            reward_mrwk="40",
            acceptance=(
                "Accepted work is a new proposed-work issue with clear evidence, "
                "duplicate search, and acceptance notes."
            ),
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(f"/bounties/{bounty.id}")

    assert response.status_code == 200
    assert "<code>/claim #649</code>" in response.text
    assert "<code>Bounty #649</code>" in response.text
    assert "<code>Refs #649</code>" in response.text
    assert "<code>Linked bounty: #649</code>" in response.text
    assert "new proposed-work GitHub issue URL" in response.text


def test_bounty_detail_warns_when_no_awards_remain(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=5,
            issue_url="https://github.com/ramimbo/mergework/issues/5",
            title="One-shot bounty",
            reward_mrwk="25",
            max_awards=1,
            acceptance="Only one accepted award should be paid.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:contributor",
            submission_url="https://github.com/ramimbo/mergework/pull/5",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(f"/bounties/{bounty.id}")

    assert response.status_code == 200
    assert (
        "No awards remain; treat new work as unpaid unless maintainers reopen the bounty."
        in response.text
    )


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
    assert (
        'href="https://github.com/ramimbo/mergework/pull/202" rel="nofollow noopener"' in page.text
    )
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
            title="Malformed award proof payload",
            reward_mrwk="100",
            acceptance="Bounty details should survive malformed stored proof JSON.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/203",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        proof_row = session.get(Proof, proof.hash)
        assert proof_row is not None
        proof_row.public_json = "{"
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    api_response = client.get(f"/api/v1/bounties/{bounty_id}")
    page = client.get(f"/bounties/{bounty_id}")

    assert api_response.status_code == 200
    assert api_response.json()["accepted_awards"] == []
    assert page.status_code == 200
    assert "Malformed award proof payload" in page.text
    assert "No accepted work has been paid for this bounty yet." in page.text


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
        unsafe_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=24,
            issue_url="https://github.com/ramimbo/mergework/issues/24",
            title="Keep rejected public URLs inert",
            reward_mrwk="25",
            acceptance="Rejected external URLs render as text, not links.",
        )
        unsafe_proof = pay_bounty(
            session,
            bounty_id=unsafe_bounty.id,
            to_account="github:contributor",
            submission_url="https://github.com/ramimbo/mergework/pull/100",
            accepted_by="maintainer",
            verifier_result={"result": "accepted"},
        )
        unsafe_payload = json.loads(unsafe_proof.public_json)
        unsafe_payload["submission_url"] = "javascript:alert(1)"
        unsafe_proof.public_json = json.dumps(
            unsafe_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        proof_hash = proof.hash
        payment_sequence = proof.ledger_sequence
        unsafe_proof_hash = unsafe_proof.hash
        unsafe_payment_sequence = unsafe_proof.ledger_sequence

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
    assert (
        'href="https://github.com/ramimbo/mergework/pull/99" rel="nofollow noopener"'
        in ledger_page.text
    )
    assert f'href="/proofs/{proof_hash}">Payment proof</a>' in ledger_page.text
    limited_ledger_page = client.get("/ledger?limit=1")
    assert limited_ledger_page.status_code == 200
    assert limited_ledger_page.text.count('class="ledger-row ledger-row--') == 1
    assert f'href="/ledger/{unsafe_payment_sequence}"' in limited_ledger_page.text
    assert f'href="/ledger/{payment_sequence}"' not in limited_ledger_page.text
    noncanonical_limit = client.get("/ledger?limit=01")
    repeated_limit = client.get("/ledger?limit=1&limit=2")
    assert noncanonical_limit.status_code == 400
    assert noncanonical_limit.json()["detail"] == "limit must be a canonical positive integer"
    assert repeated_limit.status_code == 400
    assert repeated_limit.json()["detail"] == "limit must be provided at most once"

    ledger_entry_page = client.get(f"/ledger/{payment_sequence}")
    assert ledger_entry_page.status_code == 200
    assert "Bounty Payment" in ledger_entry_page.text
    assert "Bounty scan status" in ledger_entry_page.text
    assert "Award paid" in ledger_entry_page.text
    assert 'aria-label="Ledger entry navigation"' in ledger_entry_page.text
    assert 'href="/ledger">All ledger entries</a>' in ledger_entry_page.text
    assert f'href="/ledger/{payment_sequence - 1}">Previous entry</a>' in ledger_entry_page.text
    assert f'href="/api/v1/ledger/{payment_sequence}">Entry JSON</a>' in ledger_entry_page.text
    assert f'href="/ledger/{payment_sequence + 1}">Next entry</a>' in ledger_entry_page.text
    assert (
        'href="https://github.com/ramimbo/mergework/pull/99" rel="nofollow noopener"'
        in ledger_entry_page.text
    )
    genesis_page = client.get("/ledger/1")
    assert genesis_page.status_code == 200
    assert 'href="/ledger">All ledger entries</a>' in genesis_page.text
    assert 'href="/ledger/0">Previous entry</a>' not in genesis_page.text
    assert 'href="/ledger/2">Next entry</a>' in genesis_page.text
    latest_sequence = client.get("/api/v1/ledger?limit=1").json()[0]["sequence"]
    latest_page = client.get(f"/ledger/{latest_sequence}")
    assert latest_page.status_code == 200
    assert f'href="/ledger/{latest_sequence - 1}">Previous entry</a>' in latest_page.text
    assert f'href="/ledger/{latest_sequence + 1}">Next entry</a>' not in latest_page.text
    assert client.get("/api/v1/ledger/0").status_code == 400
    assert client.get("/ledger/0").status_code == 400
    for noncanonical_sequence in (
        f"{payment_sequence}.0",
        f"+{payment_sequence}",
        f"%C2%85{payment_sequence}",
    ):
        api_response = client.get(f"/api/v1/ledger/{noncanonical_sequence}")
        assert api_response.status_code == 400
        assert api_response.json()["detail"] == "ledger sequence must be a positive integer"
        page_response = client.get(f"/ledger/{noncanonical_sequence}")
        assert page_response.status_code == 400
    leading_zero_sequence = f"0{payment_sequence}"
    leading_zero_api_response = client.get(f"/api/v1/ledger/{leading_zero_sequence}")
    assert leading_zero_api_response.status_code == 400
    assert (
        leading_zero_api_response.json()["detail"]
        == "ledger sequence must be a canonical positive integer"
    )
    leading_zero_page_response = client.get(f"/ledger/{leading_zero_sequence}")
    assert leading_zero_page_response.status_code == 400

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
    assert (
        'href="https://github.com/ramimbo/mergework/issues/23" rel="nofollow noopener"'
        in proof_page.text
    )
    assert (
        'href="https://github.com/ramimbo/mergework/pull/99" rel="nofollow noopener"'
        in proof_page.text
    )
    unsafe_proof_page = client.get(f"/proofs/{unsafe_proof_hash}")
    assert unsafe_proof_page.status_code == 200
    assert "javascript:alert(1)" in unsafe_proof_page.text
    assert 'href="javascript:alert(1)"' not in unsafe_proof_page.text
    assert "Related activity" in proof_page.text
    assert 'href="/activity?q=github%3Acontributor"' in proof_page.text
    assert f'href="/activity?q={proof_hash}"' in proof_page.text
    assert f'href="/activity?q={bounty.id}"' in proof_page.text
    assert 'href="/activity?q=https%3A//github.com/ramimbo/mergework/pull/99"' in proof_page.text

    uppercase_proof_page = client.get(f"/proofs/{proof_hash.upper()}")
    assert uppercase_proof_page.status_code == 200
    assert f'<code class="hash">{proof_hash}</code>' in uppercase_proof_page.text
    assert f'<code class="hash">{proof_hash.upper()}</code>' not in uppercase_proof_page.text

    missing_proof = client.get(f"/api/v1/proofs/{'0' * 64}")
    assert missing_proof.status_code == 404
    assert client.get("/api/v1/proofs/not-a-proof-hash").status_code == 400
    assert client.get("/proofs/not-a-proof-hash").status_code == 400


def test_ledger_entry_reference_fallbacks(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=24,
            issue_url="https://github.com/ramimbo/mergework/issues/24",
            title="Render ledger references safely",
            reward_mrwk="50",
            max_awards=2,
            acceptance="Ledger detail pages should not link unsafe references.",
        )
        empty_reference_proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:empty-reference",
            submission_url="https://github.com/ramimbo/mergework/pull/100",
            accepted_by="maintainer",
            verifier_result={"result": "accepted"},
        )
        unsafe_reference_proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:unsafe-reference",
            submission_url="https://github.com/ramimbo/mergework/pull/101",
            accepted_by="maintainer",
            verifier_result={"result": "accepted"},
        )
        empty_reference_entry = session.get(LedgerEntry, empty_reference_proof.ledger_sequence)
        unsafe_reference_entry = session.get(LedgerEntry, unsafe_reference_proof.ledger_sequence)
        assert empty_reference_entry is not None
        assert unsafe_reference_entry is not None
        empty_reference_entry.reference = ""
        unsafe_reference_entry.reference = "javascript:alert(1)"

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    empty_reference_page = client.get(f"/ledger/{empty_reference_proof.ledger_sequence}")
    assert empty_reference_page.status_code == 200
    assert "<dt>Reference</dt>" in empty_reference_page.text
    assert "<dd>-</dd>" in empty_reference_page.text

    unsafe_reference_page = client.get(f"/ledger/{unsafe_reference_proof.ledger_sequence}")
    assert unsafe_reference_page.status_code == 200
    assert "javascript:alert(1)" in unsafe_reference_page.text
    assert 'href="javascript:alert(1)"' not in unsafe_reference_page.text


def test_bounties_list_cards_have_status_pills(sqlite_url: str) -> None:
    """Bounty list cards should include a status pill for quick scanning."""
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=90,
            issue_url="https://github.com/ramimbo/mergework/issues/90",
            title="Open bounty for status pill test",
            reward_mrwk="75",
            acceptance="Should show a green status pill.",
        )
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=91,
            issue_url="https://github.com/ramimbo/mergework/issues/91",
            title="Paid bounty for status pill test",
            reward_mrwk="75",
            acceptance="Should show a blue status pill.",
        )
        pay_bounty(
            session,
            bounty_id=paid_bounty.id,
            to_account="github:tester",
            submission_url="https://github.com/ramimbo/mergework/pull/91",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    page = client.get("/bounties")
    assert page.status_code == 200

    # Status pills should appear for each card
    assert "status-pill status-open" in page.text
    assert "status-pill status-paid" in page.text

    # Cards should have status-specific class for visual distinction
    assert "bounty-card bounty-card--open" in page.text
    assert "bounty-card bounty-card--paid" in page.text

    # Reward should be highlighted
    assert "<strong>75 MRWK</strong> per award" in page.text

    # Also verify a closed bounty gets the right pill
    with session_scope(sqlite_url) as session:
        closed_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=93,
            issue_url="https://github.com/ramimbo/mergework/issues/93",
            title="Closed bounty for status pill test",
            reward_mrwk="30",
            acceptance="Should show a muted status pill.",
        )
        close_bounty(session, bounty_id=closed_bounty.id, closed_by="maintainer")

    page = client.get("/bounties")
    assert page.status_code == 200
    assert "status-pill status-closed" in page.text
    assert "bounty-card bounty-card--closed" in page.text


def test_bounty_detail_page_has_back_navigation(sqlite_url: str) -> None:
    """Bounty detail page should have a back link to the bounties list."""
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=92,
            issue_url="https://github.com/ramimbo/mergework/issues/92",
            title="Back nav test bounty",
            reward_mrwk="50",
            acceptance="Should have a back navigation link.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    page = client.get(f"/bounties/{bounty.id}")
    assert page.status_code == 200
    assert "Back to bounties" in page.text
    assert 'href="/bounties"' in page.text

    # Detail page should also have a status pill with status-specific class
    assert "status-pill status-open" in page.text
