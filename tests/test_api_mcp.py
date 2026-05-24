from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import close_bounty, create_bounty, ensure_genesis, pay_bounty
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


def test_account_api_rejects_empty_account_path(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    assert client.get("/api/v1/accounts/").status_code == 404
    account = client.get("/api/v1/accounts/github:alice")
    assert account.status_code == 200
    assert account.json()["account"] == "github:alice"


def test_bounty_api_reports_multi_award_capacity(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=11,
            issue_url="https://github.com/ramimbo/mergework/issues/11",
            title="Multi-award bounty",
            reward_mrwk="25",
            max_awards=4,
            acceptance="Each accepted submission earns one award.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    bounty = client.get("/api/v1/bounties").json()[0]

    assert bounty["reward_mrwk"] == "25"
    assert bounty["reserved_mrwk"] == "100"
    assert bounty["max_awards"] == 4
    assert bounty["awards_paid"] == 0
    assert bounty["awards_remaining"] == 4


def test_bounty_api_reports_paid_multi_award_as_exhausted(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=20,
            issue_url="https://github.com/ramimbo/mergework/issues/20",
            title="Multi-award payout edge case",
            reward_mrwk="15",
            max_awards=2,
            acceptance="Each accepted submission earns one award.",
        )
        bounty_id = bounty.id
        pay_bounty(
            session,
            bounty_id=bounty_id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/20",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        pay_bounty(
            session,
            bounty_id=bounty_id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/21",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    body = client.get(f"/api/v1/bounties/{bounty_id}").json()

    assert body["status"] == "paid"
    assert body["max_awards"] == 2
    assert body["awards_paid"] == 2
    assert body["awards_remaining"] == 0
    assert body["reserved_mrwk"] == "30"


def test_bounty_api_reports_closed_multi_award_as_unavailable(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=21,
            issue_url="https://github.com/ramimbo/mergework/issues/21",
            title="Partial close payout edge case",
            reward_mrwk="10",
            max_awards=3,
            acceptance="Each accepted submission earns one award.",
        )
        bounty_id = bounty.id
        pay_bounty(
            session,
            bounty_id=bounty_id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/22",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        close_bounty(
            session,
            bounty_id=bounty_id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/21#close",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    body = client.get(f"/api/v1/bounties/{bounty_id}").json()

    assert body["status"] == "closed"
    assert body["max_awards"] == 3
    assert body["awards_paid"] == 1
    assert body["awards_remaining"] == 0
    assert body["reserved_mrwk"] == "30"


def test_bounty_api_keeps_terminal_multi_awards_visible_but_inactive(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=36,
            issue_url="https://github.com/ramimbo/mergework/issues/36",
            title="Paid multi-award API visibility",
            reward_mrwk="8",
            max_awards=2,
            acceptance="Each accepted submission earns one award.",
        )
        closed_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=37,
            issue_url="https://github.com/ramimbo/mergework/issues/37",
            title="Closed multi-award API visibility",
            reward_mrwk="6",
            max_awards=3,
            acceptance="Close releases unpaid awards.",
        )
        paid_bounty_id = paid_bounty.id
        closed_bounty_id = closed_bounty.id

        for pull_number, login in ((36, "alice"), (37, "bob")):
            pay_bounty(
                session,
                bounty_id=paid_bounty_id,
                to_account=f"github:{login}",
                submission_url=f"https://github.com/ramimbo/mergework/pull/{pull_number}",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
            )
        pay_bounty(
            session,
            bounty_id=closed_bounty_id,
            to_account="github:carol",
            submission_url="https://github.com/ramimbo/mergework/pull/38",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        close_bounty(
            session,
            bounty_id=closed_bounty_id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/37#close",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    paid_detail = client.get(f"/api/v1/bounties/{paid_bounty_id}").json()
    closed_detail = client.get(f"/api/v1/bounties/{closed_bounty_id}").json()
    listed = {bounty["id"]: bounty for bounty in client.get("/api/v1/bounties").json()}
    status = client.get("/api/v1/status").json()

    assert paid_detail["status"] == "paid"
    assert paid_detail["awards_paid"] == 2
    assert paid_detail["awards_remaining"] == 0
    assert closed_detail["status"] == "closed"
    assert closed_detail["awards_paid"] == 1
    assert closed_detail["awards_remaining"] == 0
    assert listed[paid_bounty_id]["status"] == "paid"
    assert listed[paid_bounty_id]["awards_remaining"] == 0
    assert listed[closed_bounty_id]["status"] == "closed"
    assert listed[closed_bounty_id]["awards_remaining"] == 0
    assert status["active_bounties"] == 0


def test_bounty_api_filters_by_status(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        open_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=40,
            issue_url="https://github.com/ramimbo/mergework/issues/40",
            title="Open status filter bounty",
            reward_mrwk="5",
            acceptance="Open rows should be filterable.",
        )
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=41,
            issue_url="https://github.com/ramimbo/mergework/issues/41",
            title="Paid status filter bounty",
            reward_mrwk="5",
            acceptance="Paid rows should be filterable.",
        )
        closed_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=42,
            issue_url="https://github.com/ramimbo/mergework/issues/42",
            title="Closed status filter bounty",
            reward_mrwk="5",
            acceptance="Closed rows should be filterable.",
        )
        pay_bounty(
            session,
            bounty_id=paid_bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/41",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        close_bounty(
            session,
            bounty_id=closed_bounty.id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/42#close",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    assert [item["id"] for item in client.get("/api/v1/bounties?status=open").json()] == [
        open_bounty.id
    ]
    assert [item["id"] for item in client.get("/api/v1/bounties?status=paid").json()] == [
        paid_bounty.id
    ]
    assert [item["id"] for item in client.get("/api/v1/bounties?status=closed").json()] == [
        closed_bounty.id
    ]
    invalid = client.get("/api/v1/bounties?status=bogus")
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "status must be one of: open, paid, closed"


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


def test_mcp_rejects_malformed_requests_without_500(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    malformed = client.post("/mcp", data="not-json", headers={"content-type": "application/json"})
    assert malformed.status_code == 400
    assert malformed.json() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "parse error"},
    }

    non_object = client.post("/mcp", json=[])
    assert non_object.status_code == 400
    assert non_object.json() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "invalid request"},
    }

    bad_params = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": []},
    )
    assert bad_params.status_code == 200
    assert bad_params.json() == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {"code": -32602, "message": "invalid params"},
    }


def test_mcp_get_ledger_entry_includes_payment_proof_hash(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=45,
            issue_url="https://github.com/ramimbo/mergework/issues/45",
            title="MCP ledger proof hash",
            reward_mrwk="75",
            acceptance="MCP ledger entry agrees with REST ledger detail.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/54",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        ledger_sequence = proof.ledger_sequence
        proof_hash = proof.hash

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "get_ledger_entry",
                "arguments": {"sequence": ledger_sequence},
            },
        },
    ).json()
    payload = json.loads(result["result"]["content"][0]["text"])

    assert payload["sequence"] == ledger_sequence
    assert payload["proof_hash"] == proof_hash


def test_mcp_get_proof_returns_public_proof_details(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=37,
            issue_url="https://github.com/ramimbo/mergework/issues/37",
            title="MCP proof lookup",
            reward_mrwk="150",
            acceptance="Accepted label",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/37",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
    assert "get_proof" in {tool["name"] for tool in tools["result"]["tools"]}

    result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_proof", "arguments": {"hash": proof.hash}},
        },
    ).json()

    content = result["result"]["content"][0]
    payload = json.loads(content["text"])
    assert content["type"] == "text"
    assert payload["hash"] == proof.hash
    assert payload["kind"] == "bounty_payment"
    assert payload["ledger_sequence"] == proof.ledger_sequence
    assert payload["proof"]["repo"] == "ramimbo/mergework"
    assert payload["proof"]["submission_url"] == "https://github.com/ramimbo/mergework/pull/37"
    assert payload["proof"]["accepted_by"] == "maintainer"


def test_mcp_get_proof_reports_unknown_hash(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_proof", "arguments": {"hash": "0" * 64}},
        },
    ).json()

    assert result["result"]["content"][0]["text"] == "proof not found"


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
    assert "https://github.com/ramimbo/mergework/discussions/16" in docs
    assert "docs/paid-bounties.md" in docs
    assert "docs/api-examples.md" in docs
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
    assert 'href="/accounts/reserve:bounty:1"' in account
    assert 'href="/accounts/github:alice"' in account


def test_ledger_page_highlights_bounty_payment_and_release(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=23,
            issue_url="https://github.com/ramimbo/mergework/issues/23",
            title="Improve ledger explorer payment scanning",
            reward_mrwk="150",
            max_awards=2,
            acceptance="Ledger explorer highlights bounty payments and releases.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/24",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        close_bounty(
            session,
            bounty_id=bounty.id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/23",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    ledger = client.get("/ledger").text

    assert "Reference" in ledger
    assert "Bounty Payment" in ledger
    assert "Bounty Release" in ledger
    assert "ledger-type ledger-type--bounty-payment" in ledger
    assert "ledger-type ledger-type--bounty-release" in ledger
    assert "ledger-row ledger-row--bounty-payment" in ledger
    assert "ledger-row ledger-row--bounty-release" in ledger
    assert "ramimbo/mergework/pull/24" in ledger
    assert "ramimbo/mergework/issues/23" in ledger
    assert f"/proofs/{proof.hash}" in ledger


def test_ledger_page_uses_wrapping_entry_cards(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=33,
            issue_url="https://github.com/ramimbo/mergework/issues/33",
            title="Responsive ledger",
            reward_mrwk="25",
            acceptance="Ledger remains readable on narrow screens.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:long-contributor-name",
            submission_url=("https://github.com/ramimbo/mergework/pull/33#issuecomment-1234567890"),
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    ledger = client.get("/ledger").text

    assert 'class="ledger-list"' in ledger
    assert 'class="ledger-row ledger-row--bounty-payment"' in ledger
    assert 'class="ledger-entry-card"' in ledger
    assert 'class="ledger-card-grid"' in ledger
    assert 'class="ledger-field ledger-field--reference reference-cell"' in ledger
    assert 'class="table-scroll ledger-table-wrap"' not in ledger


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
