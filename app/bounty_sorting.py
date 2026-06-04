"""Shared bounty list sorting contract."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from app.control_chars import contains_control_character

BOUNTY_SORT_LABELS = {
    "newest": "Newest first",
    "reward": "Highest per-award reward",
    "available": "Most MRWK available",
    "awards": "Most award slots",
}
BOUNTY_SORT_OPTIONS = tuple(BOUNTY_SORT_LABELS)
BOUNTY_SORT_ERROR = f"sort must be one of: {', '.join(BOUNTY_SORT_OPTIONS)}"
BountySortKey = Callable[[dict[str, Any]], Any]


def normalize_bounty_sort(sort: str | None) -> str:
    """Return a supported bounty sort mode, defaulting blank values to newest."""
    raw_sort = sort or ""
    if contains_control_character(raw_sort):
        raise ValueError("sort must not contain control characters")
    normalized_sort = raw_sort.strip().lower()
    if not normalized_sort:
        return "newest"
    if normalized_sort not in BOUNTY_SORT_OPTIONS:
        raise ValueError(BOUNTY_SORT_ERROR)
    return normalized_sort


def _newest_sort_key(bounty: dict[str, Any]) -> int:
    """Sort newest bounties first by descending internal id."""
    return int(bounty["id"])


def _reward_sort_key(bounty: dict[str, Any]) -> tuple[Decimal, int]:
    """Sort by per-award reward, then newest bounty id."""
    return Decimal(str(bounty["reward_mrwk"])), int(bounty["id"])


def _available_sort_key(bounty: dict[str, Any]) -> tuple[Decimal, int]:
    """Sort by effective remaining MRWK pool, then newest bounty id."""
    return (
        Decimal(str(bounty.get("effective_available_mrwk", bounty["available_mrwk"]))),
        int(bounty["id"]),
    )


def _awards_sort_key(bounty: dict[str, Any]) -> tuple[int, int]:
    """Sort by effective remaining award slots, then newest bounty id."""
    return (
        int(bounty.get("effective_awards_remaining", bounty["awards_remaining"])),
        int(bounty["id"]),
    )


BOUNTY_SORT_KEYS: dict[str, BountySortKey] = {
    "newest": _newest_sort_key,
    "reward": _reward_sort_key,
    "available": _available_sort_key,
    "awards": _awards_sort_key,
}


def sort_bounties(bounties: list[dict[str, Any]], sort: str | None) -> list[dict[str, Any]]:
    """Return bounties ordered by the normalized public sort mode."""
    normalized_sort = normalize_bounty_sort(sort)
    return sorted(bounties, key=BOUNTY_SORT_KEYS[normalized_sort], reverse=True)
