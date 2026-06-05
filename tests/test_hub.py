from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.hub import (
    host_without_port,
    is_ltc_lab_host,
    ltc_lab_context,
    mergework_hub_context,
)
from app.ledger.service import ensure_genesis
from app.main import create_app


def test_host_without_port_normalizes_host_headers() -> None:
    assert host_without_port("LtcLab.Site:443") == "ltclab.site"
    assert host_without_port("www.ltclab.site") == "www.ltclab.site"
    assert host_without_port("") == ""


def test_is_ltc_lab_host_matches_root_domain_only() -> None:
    assert is_ltc_lab_host("ltclab.site")
    assert is_ltc_lab_host("www.ltclab.site:8443")
    assert not is_ltc_lab_host("mrwk.online")
    assert not is_ltc_lab_host("mrwk.ltclab.site")
    assert not is_ltc_lab_host("api.mrwk.ltclab.site")


def test_ltc_lab_context_lists_projects_without_shared_mutation() -> None:
    context = ltc_lab_context()

    assert context["site_context"] == "ltc_lab"
    assert [project["name"] for project in context["projects"]] == [
        "MergeWork",
        "MergeWork API",
        "MergeWork MCP",
    ]
    assert {project["status"] for project in context["projects"]} == {"live"}
    assert "https://mrwk.online" in {project["href"] for project in context["projects"]}
    assert "https://api.mrwk.online" in {project["href"] for project in context["projects"]}
    assert "https://mcp.mrwk.online" in {project["href"] for project in context["projects"]}

    context["projects"][0]["status"] = "changed"

    assert ltc_lab_context()["projects"][0]["status"] == "live"


def test_mergework_hub_context_preserves_status_and_base_url() -> None:
    status = {"ledger_height": 7, "active_bounties": 2}

    context = mergework_hub_context(status, "https://mrwk.online")

    assert context["status"] is status
    assert context["public_base_url"] == "https://mrwk.online"
    assert context["contributor_starting_points"] == [
        {
            "title": "Effectively open bounties",
            "href": "/bounties?status=open&availability=effectively_open",
            "description": "Start from live bounty rows that still have effective award capacity.",
        },
        {
            "title": "Accepted work activity",
            "href": "/activity",
            "description": (
                "Check proof-backed paid work and pending payout queues before claiming payment."
            ),
        },
        {
            "title": "Current bounty JSON",
            "href": "/api/v1/bounties?status=open&availability=effectively_open",
            "description": "Use the public API to verify live status, capacity, and requirements.",
        },
    ]

    context["contributor_starting_points"][0]["title"] = "changed"

    assert (
        mergework_hub_context(status, "https://mrwk.online")["contributor_starting_points"][0][
            "title"
        ]
        == "Effectively open bounties"
    )


def test_mergework_hub_renders_contributor_starting_points(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/")

    assert response.status_code == 200
    assert "Contributor starting points" in response.text
    assert "Effectively open bounties" in response.text
    assert 'href="/bounties?status=open&amp;availability=effectively_open"' in response.text
    assert "Accepted work activity" in response.text
    assert 'href="/activity"' in response.text
    assert "Current bounty JSON" in response.text
    assert 'href="/api/v1/bounties?status=open&amp;availability=effectively_open"' in response.text
