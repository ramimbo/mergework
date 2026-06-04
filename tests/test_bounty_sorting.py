from __future__ import annotations

import pytest

from app.bounty_sorting import BOUNTY_SORT_ERROR, normalize_bounty_sort, sort_bounties


def _bounty_row(
    bounty_id: int,
    *,
    reward_mrwk: str,
    available_mrwk: str,
    awards_remaining: int,
    effective_available_mrwk: str | None = None,
    effective_awards_remaining: int | None = None,
) -> dict[str, object]:
    return {
        "id": bounty_id,
        "reward_mrwk": reward_mrwk,
        "available_mrwk": available_mrwk,
        "effective_available_mrwk": effective_available_mrwk or available_mrwk,
        "awards_remaining": awards_remaining,
        "effective_awards_remaining": (
            effective_awards_remaining
            if effective_awards_remaining is not None
            else awards_remaining
        ),
    }


def _ids(rows: list[dict[str, object]]) -> list[int]:
    return [int(row["id"]) for row in rows]


@pytest.mark.parametrize(
    ("raw_sort", "expected"),
    [
        (None, "newest"),
        ("", "newest"),
        (" reward ", "reward"),
        ("AVAILABLE", "available"),
        ("awards", "awards"),
    ],
)
def test_normalize_bounty_sort(raw_sort: str | None, expected: str) -> None:
    assert normalize_bounty_sort(raw_sort) == expected


def test_normalize_bounty_sort_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match=BOUNTY_SORT_ERROR):
        normalize_bounty_sort("oldest")


def test_normalize_bounty_sort_rejects_control_characters() -> None:
    with pytest.raises(ValueError, match="sort must not contain control characters"):
        normalize_bounty_sort("\x85reward")


def test_sort_bounties_preserves_supported_orders() -> None:
    rows = [
        _bounty_row(
            1,
            reward_mrwk="10",
            available_mrwk="100",
            awards_remaining=10,
            effective_available_mrwk="20",
            effective_awards_remaining=2,
        ),
        _bounty_row(
            2,
            reward_mrwk="25",
            available_mrwk="25",
            awards_remaining=1,
            effective_available_mrwk="25",
            effective_awards_remaining=1,
        ),
        _bounty_row(
            3,
            reward_mrwk="25",
            available_mrwk="75",
            awards_remaining=3,
            effective_available_mrwk="75",
            effective_awards_remaining=3,
        ),
    ]

    assert _ids(sort_bounties(rows, None)) == [3, 2, 1]
    assert _ids(sort_bounties(rows, "reward")) == [3, 2, 1]
    assert _ids(sort_bounties(rows, "available")) == [3, 2, 1]
    assert _ids(sort_bounties(rows, "awards")) == [3, 1, 2]
