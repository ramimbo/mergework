from __future__ import annotations

import pytest

from app.bounty_sorting import normalize_bounty_sort, sort_bounties


def _bounty(
    bounty_id: int,
    *,
    reward_mrwk: str = "10",
    available_mrwk: str = "10",
    awards_remaining: int = 1,
    effective_available_mrwk: str | None = None,
    effective_awards_remaining: int | None = None,
) -> dict[str, object]:
    bounty: dict[str, object] = {
        "id": bounty_id,
        "reward_mrwk": reward_mrwk,
        "available_mrwk": available_mrwk,
        "awards_remaining": awards_remaining,
    }
    if effective_available_mrwk is not None:
        bounty["effective_available_mrwk"] = effective_available_mrwk
    if effective_awards_remaining is not None:
        bounty["effective_awards_remaining"] = effective_awards_remaining
    return bounty


def _ids(bounties: list[dict[str, object]]) -> list[int]:
    return [int(bounty["id"]) for bounty in bounties]


def test_normalize_bounty_sort_defaults_and_validates() -> None:
    assert normalize_bounty_sort(None) == "newest"
    assert normalize_bounty_sort(" Reward ") == "reward"

    with pytest.raises(ValueError, match="sort must not contain control characters"):
        normalize_bounty_sort("\x85reward")

    with pytest.raises(ValueError, match="sort must be one of"):
        normalize_bounty_sort("oldest")


def test_sort_bounties_uses_shared_sort_keys() -> None:
    bounties = [
        _bounty(1, reward_mrwk="25", available_mrwk="75", awards_remaining=3),
        _bounty(3, reward_mrwk="10", available_mrwk="90", awards_remaining=4),
        _bounty(
            2,
            reward_mrwk="25",
            available_mrwk="50",
            awards_remaining=5,
            effective_available_mrwk="10",
            effective_awards_remaining=1,
        ),
    ]

    assert _ids(sort_bounties(bounties, "newest")) == [3, 2, 1]
    assert _ids(sort_bounties(bounties, "reward")) == [2, 1, 3]
    assert _ids(sort_bounties(bounties, "available")) == [3, 1, 2]
    assert _ids(sort_bounties(bounties, "awards")) == [3, 1, 2]
