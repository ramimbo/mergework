"""Shared effective-availability filtering for bounty discovery surfaces."""

from __future__ import annotations

from typing import Any

from app.control_chars import contains_control_character

BOUNTY_AVAILABILITY_FILTERS = {"all", "effectively_open"}
BOUNTY_AVAILABILITY_ERROR = "availability must be one of: all, effectively_open"


def normalize_bounty_availability_filter(availability: str | None) -> str:
    raw_availability = availability or ""
    if contains_control_character(raw_availability):
        raise ValueError("availability must not contain control characters")
    normalized = raw_availability.strip().lower()
    if not normalized:
        return "all"
    if normalized not in BOUNTY_AVAILABILITY_FILTERS:
        raise ValueError(BOUNTY_AVAILABILITY_ERROR)
    return normalized


def filter_bounties_by_availability(
    bounties: list[dict[str, Any]], availability: str | None
) -> list[dict[str, Any]]:
    normalized = normalize_bounty_availability_filter(availability)
    if normalized == "all":
        return bounties
    return [
        bounty
        for bounty in bounties
        if int(bounty.get("effective_awards_remaining", bounty["awards_remaining"])) > 0
    ]
