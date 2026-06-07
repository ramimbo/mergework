from __future__ import annotations

from app.activity import activity_context
from app.db import create_schema, session_scope


def test_activity_context_preserves_empty_feed_shape(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        assert activity_context(session) == {
            "query": "",
            "sort": "mrwk",
            "sort_options": {
                "mrwk": "Most accepted MRWK",
                "awards": "Most accepted awards",
                "account": "Account A\u2013Z",
                "recent": "Most recent accepted work",
            },
            "selected_sort": "mrwk",
            "api_activity_url": "/api/v1/activity",
            "clear_activity_url": "/activity",
            "account_page_url": None,
            "sort_urls": {
                "mrwk": "/activity",
                "awards": "/activity?sort=awards",
                "account": "/activity?sort=account",
                "recent": "/activity?sort=recent",
            },
            "totals": {
                "accepted_awards": 0,
                "accepted_mrwk": "0",
                "contributors": 0,
            },
            "pending_totals": {
                "pending_awards": 0,
                "pending_mrwk": "0",
            },
            "contributors": [],
            "pending_payouts": [],
            "recent": [],
        }
