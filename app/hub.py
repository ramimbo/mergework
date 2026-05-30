from __future__ import annotations

from typing import Any

LTC_LAB_HOSTS = {"ltclab.site", "www.ltclab.site"}
LTC_LAB_PROJECTS = (
    {
        "name": "MergeWork",
        "tagline": "MRWK from LTC Lab",
        "href": "https://mrwk.online",
        "status": "live",
    },
    {
        "name": "MergeWork API",
        "tagline": "Public MRWK status, bounty, ledger, and proof endpoints",
        "href": "https://api.mrwk.online",
        "status": "live",
    },
    {
        "name": "MergeWork MCP",
        "tagline": "Tool endpoint for bounty and ledger queries",
        "href": "https://mcp.mrwk.online",
        "status": "live",
    },
)


def host_without_port(host_header: str) -> str:
    return host_header.split(":", 1)[0].lower()


def is_ltc_lab_host(host_header: str) -> bool:
    return host_without_port(host_header) in LTC_LAB_HOSTS


def ltc_lab_context() -> dict[str, Any]:
    return {
        "site_context": "ltc_lab",
        "projects": [dict(project) for project in LTC_LAB_PROJECTS],
    }


def mergework_hub_context(status: dict[str, Any], public_base_url: str) -> dict[str, Any]:
    return {
        "status": status,
        "public_base_url": public_base_url,
    }
