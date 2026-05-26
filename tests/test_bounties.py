from __future__ import annotations

import pytest

from app.bounties import issue_number_search_value, search_bounties
from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty


def test_search_bounties_filters_status_text_and_issue_number(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        open_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=72,
            issue_url="https://github.com/ramimbo/mergework/issues/72",
            title="Public bounty discovery",
            reward_mrwk="50",
            acceptance="Find review-ready public work.",
        )
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=73,
            issue_url="https://github.com/ramimbo/mergework/issues/73",
            title="Paid bounty discovery",
            reward_mrwk="50",
            acceptance="Closed out after accepted work.",
        )
        pay_bounty(
            session,
            bounty_id=paid_bounty.id,
            to_account="github:contributor",
            submission_url="https://github.com/ramimbo/mergework/pull/73",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        assert [bounty.id for bounty in search_bounties(session, status=" OPEN ")] == [
            open_bounty.id
        ]
        assert [bounty.id for bounty in search_bounties(session, status="paid")] == [paid_bounty.id]
        assert [
            bounty.issue_number for bounty in search_bounties(session, query_text="review-ready")
        ] == [72]
        assert [bounty.issue_number for bounty in search_bounties(session, query_text="73")] == [73]


def test_search_bounties_escapes_like_wildcards(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=80,
            issue_url="https://github.com/ramimbo/mergework/issues/80",
            title="Literal 100% release_note path",
            reward_mrwk="50",
            acceptance=r"Document C:\work\mergework examples.",
        )
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=81,
            issue_url="https://github.com/ramimbo/mergework/issues/81",
            title="Plain text bounty",
            reward_mrwk="50",
            acceptance="No wildcard terms.",
        )

        assert [bounty.issue_number for bounty in search_bounties(session, query_text="%")] == [80]
        assert [bounty.issue_number for bounty in search_bounties(session, query_text="_")] == [80]
        assert [bounty.issue_number for bounty in search_bounties(session, query_text="\\")] == [80]


def test_search_bounties_rejects_invalid_status_and_ignores_oversized_issue() -> None:
    assert issue_number_search_value("9" * 40) is None
    with (
        pytest.raises(ValueError, match="status must be one of: open, paid, closed"),
        session_scope("sqlite:///:memory:") as session,
    ):
        search_bounties(session, status="bogus")
