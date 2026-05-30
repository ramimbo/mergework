from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.public_routes import public_bounties_context


def test_public_bounties_context_normalizes_filter_state() -> None:
    bounties = [
        {
            "id": 1,
            "status": "open",
            "awards_remaining": 2,
            "reward_mrwk": "25",
        }
    ]

    context = public_bounties_context(bounties, status=" OPEN ", q=" proof ", sort=" Reward ")

    assert context == {
        "bounties": bounties,
        "summary": {
            "bounties_shown": 1,
            "open_awards": 2,
            "open_pool_mrwk": "50",
        },
        "selected_status": "open",
        "query_text": "proof",
        "selected_sort": "reward",
        "sort_options": {
            "newest": "Newest first",
            "reward": "Highest per-award reward",
            "available": "Most MRWK available",
            "awards": "Most award slots",
        },
        "selected_limit": None,
        "limit_options": (10, 25, 50, 100, 200),
        "api_results_url": "/api/v1/bounties?status=open&q=proof&sort=reward",
    }


def test_public_bounties_context_preserves_limited_json_results_url() -> None:
    context = public_bounties_context([], status=None, q="issue #580", sort="newest", limit=25)

    assert context["api_results_url"] == "/api/v1/bounties?q=issue+%23580&limit=25"


def test_docs_page_marks_static_github_links_as_untrusted(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    page = client.get("/docs")

    assert page.status_code == 200
    for url in (
        "https://ltclab.site",
        "https://mrwk.online",
        "https://api.mrwk.online",
        "https://mcp.mrwk.online",
        "https://mrwk.ltclab.site",
        "https://api.mrwk.ltclab.site",
        "https://mcp.mrwk.ltclab.site",
        "https://github.com/ramimbo/mergework/discussions/16",
        "https://github.com/ramimbo/mergework/blob/main/docs/bounty-rules.md",
        "https://github.com/ramimbo/mergework/blob/main/docs/paid-bounties.md",
        "https://github.com/ramimbo/mergework/blob/main/docs/agent-guide.md",
        "https://github.com/ramimbo/mergework/blob/main/docs/api-examples.md",
        "https://github.com/ramimbo/mergework/blob/main/docs/ledger.md",
    ):
        assert f'href="{url}" rel="nofollow noopener"' in page.text


def test_ltc_lab_header_marks_github_nav_link_as_untrusted(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    page = client.get("/", headers={"host": "ltclab.site"})

    assert page.status_code == 200
    assert ('href="https://mrwk.online" rel="nofollow noopener"') in page.text
    assert ('href="https://github.com/ramimbo/mergework" rel="nofollow noopener"') in page.text


def test_hub_marks_static_service_links_as_untrusted(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    page = client.get("/")

    assert page.status_code == 200
    for url in (
        "https://ltclab.site",
        "https://api.mrwk.online",
        "https://mcp.mrwk.online",
    ):
        assert f'href="{url}" rel="nofollow noopener"' in page.text


def test_ltc_lab_project_links_are_marked_untrusted(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    page = client.get("/", headers={"host": "ltclab.site"})

    assert page.status_code == 200
    for url in (
        "https://mrwk.online",
        "https://api.mrwk.online",
        "https://mcp.mrwk.online",
    ):
        assert f'href="{url}" rel="nofollow noopener"' in page.text
