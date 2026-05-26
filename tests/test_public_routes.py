from __future__ import annotations

from app.public_routes import public_bounties_context


def test_public_bounties_context_normalizes_filter_state() -> None:
    bounties = [
        {
            "id": 1,
            "status": "open",
            "awards_remaining": 2,
            "reward_mrwk": "25",
        }
    ]

    context = public_bounties_context(bounties, status=" OPEN ", q=" proof ", sort=" Reward ")

    assert context == {
        "bounties": bounties,
        "summary": {
            "bounties_shown": 1,
            "open_awards": 2,
            "open_pool_mrwk": "50",
        },
        "selected_status": "open",
        "query_text": "proof",
        "selected_sort": "reward",
        "sort_options": {
            "newest": "Newest first",
            "reward": "Highest per-award reward",
            "available": "Most MRWK available",
            "awards": "Most award slots",
        },
    }

    blank_context = public_bounties_context(bounties, status="   ", q=None)

    assert blank_context["selected_status"] is None
    assert blank_context["query_text"] == ""
