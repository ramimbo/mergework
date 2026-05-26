from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import ensure_genesis
from app.main import create_app
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


def test_hub_clarifies_current_and_future_transfer_paths(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/")

    assert response.status_code == 200
    assert "Supported paths today are GitHub balance claims" in response.text
    assert "linked <code>mrwk1</code> wallet payouts" in response.text
    assert "does not currently operate a public BTC, USDC, fiat, bridge" in response.text
    assert "exchange, or off-ramp" in response.text
    assert "Future public snapshots, bridges, and onchain claims" in response.text
    assert "separate maintainer/contributor discussion" in response.text
