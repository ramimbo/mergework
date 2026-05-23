from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty
from app.main import create_app


def test_health_status_and_bounty_api(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=1,
            issue_url="https://github.com/ramimbo/mergework/issues/1",
            title="First bounty",
            reward_mrwk="75",
            acceptance="Accepted label",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    assert client.get("/health").json()["ok"] is True
    status = client.get("/api/v1/status").json()
    assert status["ticker"] == "MRWK"
    assert status["ledger_height"] == 2
    assert status["active_bounties"] == 1
    bounties = client.get("/api/v1/bounties").json()
    assert bounties[0]["title"] == "First bounty"


def test_mcp_tools_list_and_call(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
    assert tools["result"]["tools"][0]["name"] == "list_bounties"

    balance = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_balance", "arguments": {"account": "treasury:mrwk"}},
        },
    ).json()
    assert balance["result"]["content"][0]["type"] == "text"
    assert "100000000" in balance["result"]["content"][0]["text"]


def test_host_specific_homepages(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    lab = client.get("/", headers={"host": "ltclab.site"}).text
    mrwk = client.get("/", headers={"host": "mrwk.ltclab.site"}).text

    assert "LTC Lab" in lab
    assert "MRWK from LTC Lab" in lab
    assert "https://api.mrwk.ltclab.site" in lab
    assert "https://mcp.mrwk.ltclab.site" in lab
    assert "Open-source work, recorded as MRWK" in mrwk
    assert "MRWK from LTC Lab" in mrwk
    assert "https://api.mrwk.ltclab.site" in mrwk
    assert "https://mcp.mrwk.ltclab.site" in mrwk


def test_docs_page_lists_live_ltclab_urls(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    docs = client.get("/docs").text
    api_docs = client.get("/api/docs").text

    assert "https://ltclab.site" in docs
    assert "https://mrwk.ltclab.site" in docs
    assert "https://api.mrwk.ltclab.site" in docs
    assert "https://mcp.mrwk.ltclab.site" in docs
    assert "OpenAPI docs" in docs
    assert "SwaggerUIBundle" in api_docs


def test_explorer_links_ledger_proof_and_account(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=2,
            issue_url="https://github.com/ramimbo/mergework/issues/2",
            title="Explorer test",
            reward_mrwk="25",
            acceptance="Accepted label",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/3",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    ledger = client.get("/ledger").text
    entry = client.get("/ledger/3").text
    proof_page = client.get(f"/proofs/{proof.hash}").text
    account = client.get("/accounts/github:alice").text

    assert "/ledger/3" in ledger
    assert f"/proofs/{proof.hash}" in ledger
    assert "Entry hash" in ledger
    assert "Previous hash" in entry
    assert "Proof hash" in entry
    assert proof.hash in entry
    assert "Accepted by" in proof_page
    assert "Issue" in proof_page
    assert "ramimbo/mergework #2" in proof_page
    assert proof.hash in proof_page
    assert "maintainer" in proof_page
    assert "github:alice" in proof_page
    assert "Ledger address" in account
    assert "MRWK wallet transfers are enabled" in account


def test_mcp_can_register_and_fetch_wallet(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
    tool_names = {tool["name"] for tool in tools["result"]["tools"]}
    assert "register_wallet" in tool_names
    assert "get_wallet" in tool_names

    public_key_hex = "22" * 32
    registered = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "register_wallet",
                "arguments": {"public_key_hex": public_key_hex, "label": "MCP wallet"},
            },
        },
    ).json()
    assert "mrwk1" in registered["result"]["content"][0]["text"]
