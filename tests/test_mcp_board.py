from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis
from app.main import create_app
from app.treasury import propose_treasury_action


def test_mcp_bounty_board_resource(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        # Create a bounty that is claimable
        b1 = create_bounty(
            session,
            repo="org/repo",
            issue_number=101,
            issue_url="https://github.com/org/repo/issues/101",
            title="Claimable Now",
            reward_mrwk="100",
            acceptance="Logic",
        )
        # Create a pending create_bounty proposal (opening soon)
        propose_treasury_action(
            session,
            action="create_bounty",
            payload={
                "repo": "org/repo",
                "issue_number": 102,
                "issue_url": "https://github.com/org/repo/issues/102",
                "title": "Opening Soon",
                "reward_mrwk": "200",
                "acceptance": "Logic",
            },
            proposed_by="maintainer",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    # Test resources/list
    resources = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"}
    ).json()
    assert resources["result"]["resources"][0]["uri"] == "bounties://active"

    # Test resources/read
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "resources/read",
            "params": {"uri": "bounties://active"},
        },
    ).json()

    assert "result" in response
    content = json.loads(response["result"]["contents"][0]["text"])

    assert "claimable_now" in content
    assert "opening_soon" in content
    assert "treasury" in content

    assert content["claimable_now"][0]["bounty_id"] == b1.id

    assert content["opening_soon"][0]["title"] == "Opening Soon"
    assert content["opening_soon"][0]["reward_mrwk"] == "200"
    assert isinstance(content["treasury"]["balance_mrwk"], str)

    assert content["treasury"]["active_liabilities_mrwk"] == "100"


def test_mcp_read_resource_requires_params_dict(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": "not-a-dict"}
    ).json()

    assert response["error"]["code"] == -32602
    assert "invalid params" in response["error"]["message"]
