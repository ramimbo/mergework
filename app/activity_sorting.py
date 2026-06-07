"""Shared activity contributor sorting contract.

The /activity endpoint already supports a `q` free-text filter and an `account`
scope. This module adds a `sort` option so contributors can be ordered by:

- ``mrwk`` (default): highest accepted_mrwk first, account as tie-breaker.
- ``awards``: most accepted awards first, then mrwk, then account.
- ``account``: alphabetical by account address.
- ``recent``: highest latest ledger sequence first, then mrwk, then account.

The default ``mrwk`` ordering is intentionally identical to the historical
hard-coded sort in :func:`app.serializers.activity_to_dict`, so the change is
strictly additive for callers that omit ``sort``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.control_chars import contains_control_character

ACTIVITY_SORT_LABELS = {
    "mrwk": "Most accepted MRWK",
    "awards": "Most accepted awards",
    "account": "Account A–Z",
    "recent": "Most recent accepted work",
}
ACTIVITY_SORT_OPTIONS = tuple(ACTIVITY_SORT_LABELS)
ACTIVITY_SORT_ERROR = f"sort must be one of: {', '.join(ACTIVITY_SORT_OPTIONS)}"
_ContributorSortKey = Callable[[dict[str, Any]], Any]


def _accepted_mrwk(contributor: dict[str, Any]) -> int:
    """Parse the formatted ``accepted_mrwk`` string back to whole-MRWK for sorting.

    The serializer formats amounts via :func:`app.ledger.service.format_mrwk`
    which is a human-readable string (e.g. ``"395"`` or ``"1.5"``). Sorting on
    that string would be wrong (``"9"`` > ``"10"``), so we keep the integer
    micro-unit value on each contributor until after the sort and only strip
    it when serialising.
    """

    return int(contributor.get("accepted_microunits", 0))


def _accepted_awards(contributor: dict[str, Any]) -> int:
    return int(contributor.get("accepted_awards", 0))


def _account(contributor: dict[str, Any]) -> str:
    return str(contributor["account"])


def _latest_sequence(contributor: dict[str, Any]) -> int:
    return int(contributor.get("latest_ledger_sequence", 0))


def _mrwk_sort_key(contributor: dict[str, Any]) -> tuple[int, str]:
    return (-_accepted_mrwk(contributor), _account(contributor))


def _awards_sort_key(contributor: dict[str, Any]) -> tuple[int, int, str]:
    return (
        -_accepted_awards(contributor),
        -_accepted_mrwk(contributor),
        _account(contributor),
    )


def _account_sort_key(contributor: dict[str, Any]) -> str:
    return _account(contributor)


def _recent_sort_key(contributor: dict[str, Any]) -> tuple[int, int, str]:
    return (
        -_latest_sequence(contributor),
        -_accepted_mrwk(contributor),
        _account(contributor),
    )


_ACTIVITY_SORT_KEYS: dict[str, _ContributorSortKey] = {
    "mrwk": _mrwk_sort_key,
    "awards": _awards_sort_key,
    "account": _account_sort_key,
    "recent": _recent_sort_key,
}


def normalize_activity_sort(sort: str | None) -> str:
    raw_sort = sort or ""
    if contains_control_character(raw_sort):
        raise ValueError("sort must not contain control characters")
    normalized_sort = raw_sort.strip().lower()
    if not normalized_sort:
        return "mrwk"
    if normalized_sort not in ACTIVITY_SORT_OPTIONS:
        raise ValueError(ACTIVITY_SORT_ERROR)
    return normalized_sort


def sort_activity_contributors(
    contributors: list[dict[str, Any]], sort: str | None
) -> list[dict[str, Any]]:
    normalized_sort = normalize_activity_sort(sort)
    return sorted(contributors, key=_ACTIVITY_SORT_KEYS[normalized_sort])
