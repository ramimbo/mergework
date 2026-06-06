from __future__ import annotations

import pytest

from app.bounty_availability import (
    BOUNTY_AVAILABILITY_ERROR,
    filter_bounties_by_availability,
    normalize_bounty_availability_filter,
)


def test_bounty_availability_filter_defaults_and_normalizes_values() -> None:
    assert normalize_bounty_availability_filter(None) == "all"
    assert normalize_bounty_availability_filter("") == "all"
    assert normalize_bounty_availability_filter("  EFFECTIVELY_OPEN  ") == "effectively_open"


def test_bounty_availability_filter_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match=BOUNTY_AVAILABILITY_ERROR):
        normalize_bounty_availability_filter("open")

    with pytest.raises(ValueError, match="availability must not contain control characters"):
        normalize_bounty_availability_filter("effectively_open\x85")


def test_filter_bounties_by_availability_uses_effective_capacity() -> None:
    bounties = [
        {"id": 1, "awards_remaining": 3, "effective_awards_remaining": 0},
        {"id": 2, "awards_remaining": 0, "effective_awards_remaining": 1},
        {"id": 3, "awards_remaining": 2},
    ]

    assert filter_bounties_by_availability(bounties, "all") == bounties
    assert filter_bounties_by_availability(bounties, "effectively_open") == [
        bounties[1],
        bounties[2],
    ]
