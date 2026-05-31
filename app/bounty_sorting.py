"""Shared bounty list sorting contract."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.ledger.service import CONTROL_CHAR_RE

BOUNTY_SORT_LABELS = {
    "newest": "Newest first",
    "reward": "Highest per-award reward",
    "available": "Most MRWK available",
    "awards": "Most award slots",
}
BOUNTY_SORT_OPTIONS = tuple(BOUNTY_SORT_LABELS)
BOUNTY_SORT_ERROR = f"sort must be one of: {', '.join(BOUNTY_SORT_OPTIONS)}"
BOUNTY_SORT_CONTROL_CHAR_ERROR = "sort must not contain control characters"


def normalize_bounty_sort(sort: str | None) -> str:
    if sort is not None and CONTROL_CHAR_RE.search(sort):
        raise ValueError(BOUNTY_SORT_CONTROL_CHAR_ERROR)
    normalized_sort = (sort or "").strip().lower()
    if not normalized_sort:
        return "newest"
    if normalized_sort not in BOUNTY_SORT_OPTIONS:
        raise ValueError(BOUNTY_SORT_ERROR)
    return normalized_sort


def sort_bounties(bounties: list[dict[str, Any]], sort: str | None) -> list[dict[str, Any]]:
    normalized_sort = normalize_bounty_sort(sort)
    if normalized_sort == "newest":
        return sorted(bounties, key=lambda bounty: int(bounty["id"]), reverse=True)
    if normalized_sort == "reward":
        return sorted(
            bounties,
            key=lambda bounty: (Decimal(str(bounty["reward_mrwk"])), int(bounty["id"])),
            reverse=True,
        )
    if normalized_sort == "available":
        return sorted(
            bounties,
            key=lambda bounty: (Decimal(str(bounty["available_mrwk"])), int(bounty["id"])),
            reverse=True,
        )
    return sorted(
        bounties,
        key=lambda bounty: (int(bounty["awards_remaining"]), int(bounty["id"])),
        reverse=True,
    )
