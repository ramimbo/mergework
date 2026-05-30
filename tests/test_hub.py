from __future__ import annotations

from app.hub import (
    host_without_port,
    is_ltc_lab_host,
    ltc_lab_context,
    mergework_hub_context,
)


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

    assert context == {
        "status": status,
        "public_base_url": "https://mrwk.online",
    }
