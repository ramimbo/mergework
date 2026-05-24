from __future__ import annotations

import json

import pytest
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


@pytest.mark.parametrize("limit", ["0", "-1", "201"])
def test_ledger_api_rejects_out_of_range_limits(sqlite_url: str, limit: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(f"/api/v1/ledger?limit={limit}")

    assert response.status_code == 422


def test_head_requests_match_get_routes_without_body(sqlite_url: str) -> None:
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

    for path in ("/", "/docs", "/api/v1/status", "/api/v1/bounties"):
        response = client.head(path)
        assert response.status_code == 200
        assert response.content == b""

    post_only = client.head("/api/v1/bounties/1/pay")
    assert post_only.status_code == 405
    assert post_only.headers["allow"] == "POST"


def test_trailing_slash_redirects_keep_forwarded_https_scheme(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=103,
            issue_url="https://github.com/ramimbo/mergework/issues/103",
            title="Public UX bounty",
            reward_mrwk="75",
            acceptance="Trailing slash redirects should keep HTTPS on public hosts.",
        )

    public_client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="http://mrwk.ltclab.site",
    )
    api_client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="http://api.mrwk.ltclab.site",
    )

    public_page = public_client.get(
        f"/bounties/{bounty.id}/",
        headers={"x-forwarded-proto": "https"},
        follow_redirects=False,
    )
    public_api = public_client.get(
        f"/api/v1/bounties/{bounty.id}/",
        headers={"x-forwarded-proto": "https"},
        follow_redirects=False,
    )
    api_host = api_client.get(
        f"/api/v1/bounties/{bounty.id}/",
        headers={"x-forwarded-proto": "https"},
        follow_redirects=False,
    )

    assert public_page.status_code == 307
    assert public_page.headers["location"] == f"https://mrwk.ltclab.site/bounties/{bounty.id}"
    assert public_api.status_code == 307
    assert public_api.headers["location"] == f"https://mrwk.ltclab.site/api/v1/bounties/{bounty.id}"
    assert api_host.status_code == 307
    assert (
        api_host.headers["location"] == f"https://api.mrwk.ltclab.site/api/v1/bounties/{bounty.id}"
    )


def test_account_api_rejects_empty_account_path(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    assert client.get("/api/v1/accounts/").status_code == 404
    account = client.get("/api/v1/accounts/github:alice")
    assert account.status_code == 200
    assert account.json()["account"] == "github:alice"


def test_account_api_reports_internal_ledger_account_transfer_status(sqlite_url: str) -> None:
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

    treasury = client.get("/api/v1/accounts/treasury:mrwk").json()
    reserve = client.get("/api/v1/accounts/reserve:bounty:1").json()

    assert treasury["exists"] is True
    assert reserve["exists"] is True
    assert treasury["transfer_status"] == (
        "Internal ledger account. MRWK wallet transfers are only available "
        "for registered mrwk1 addresses."
    )
    assert reserve["transfer_status"] == treasury["transfer_status"]


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
    assert [item["id"] for item in client.get("/api/v1/bounties?status=OPEN").json()] == [
        open_bounty.id
    ]
    assert [item["id"] for item in client.get("/api/v1/bounties?status= Paid ").json()] == [
        paid_bounty.id
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


@pytest.mark.parametrize(
    ("arguments", "request_id"),
    [
        ([], 8),
        ("", 9),
        (0, 10),
        (False, 11),
    ],
    ids=["array", "empty-string", "zero", "false"],
)
def test_mcp_rejects_non_object_tool_arguments(
    sqlite_url: str, arguments: object, request_id: int
) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": "list_bounties", "arguments": arguments},
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32602, "message": "invalid params"},
    }


def test_mcp_rejects_unknown_tool_name(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "definitely_unknown", "arguments": {}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 12,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }


def test_mcp_get_bounty_rejects_fractional_id(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=103,
            issue_url="https://github.com/ramimbo/mergework/issues/103",
            title="MCP bounty id validation",
            reward_mrwk="75",
            acceptance="MCP tools should reject fractional ids.",
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "get_bounty", "arguments": {"id": bounty_id + 0.9}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 12,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }


@pytest.mark.parametrize("bounty_id", [0, -1, "0", "-1"])
def test_mcp_get_bounty_rejects_non_positive_id(sqlite_url: str, bounty_id: object) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "get_bounty", "arguments": {"id": bounty_id}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 12,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }


@pytest.mark.parametrize(
    ("tool_name", "arguments", "request_id"),
    [
        ("get_balance", {"account": True}, 13),
        ("get_balance", {"account": 123}, 14),
        ("get_balance", {"account": ""}, 15),
        ("get_wallet", {"address": 123}, 16),
        ("get_proof", {"hash": 123}, 17),
    ],
)
def test_mcp_rejects_invalid_string_arguments(
    sqlite_url: str, tool_name: str, arguments: dict[str, object], request_id: int
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32602, "message": "invalid tool arguments"},
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
    assert 'href="/activity"' in docs
    assert 'href="/api/v1/activity"' in docs
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
    account_api = client.get("/api/v1/accounts/github:alice").json()
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
    assert account_api["transfer_status"] == (
        "Claim GitHub balances from /me after linking a registered mrwk1 wallet."
    )
    assert "Claim GitHub balances from /me" in account
    assert 'href="https://github.com/alice">@alice</a>' in account
    assert 'href="/accounts/reserve:bounty:1"' in account
    assert 'href="/accounts/github:alice"' in account


def test_account_page_rejects_empty_account_path(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/accounts/")

    assert response.status_code == 404


def test_wallet_account_views_normalize_mixed_case_addresses(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    wallet_address = "mrwk1" + "a" * 40
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        proof = pay_bounty(
            session,
            bounty_id=create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=50,
                issue_url="https://github.com/ramimbo/mergework/issues/50",
                title="Normalize wallet account links",
                reward_mrwk="50",
                acceptance="Wallet account views normalize address casing.",
            ).id,
            to_account=wallet_address,
            submission_url="https://github.com/ramimbo/mergework/pull/50",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    mixed_case_address = "MRWK1" + "A" * 40

    account = client.get(f"/accounts/{mixed_case_address}").text
    balance = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_balance", "arguments": {"account": mixed_case_address}},
        },
    ).json()

    assert "50 MRWK" in account
    assert f"/proofs/{proof.hash}" in account
    assert f"{wallet_address}: 50 MRWK" in balance["result"]["content"][0]["text"]


def test_github_account_views_normalize_mixed_case_logins(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        proof = pay_bounty(
            session,
            bounty_id=create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=103,
                issue_url="https://github.com/ramimbo/mergework/issues/103",
                title="Normalize GitHub account links",
                reward_mrwk="50",
                acceptance="GitHub account views normalize login casing.",
            ).id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/103",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    account_cases = (
        ("github:Alice", "/api/v1/accounts/github:Alice", "/accounts/github:Alice"),
        ("GitHub:Alice", "/api/v1/accounts/GitHub:Alice", "/accounts/GitHub:Alice"),
        (" GitHub:Alice ", "/api/v1/accounts/%20GitHub:Alice%20", "/accounts/%20GitHub:Alice%20"),
    )
    for mcp_account, api_path, page_path in account_cases:
        account_api = client.get(api_path).json()
        account = client.get(page_path).text
        balance = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_balance", "arguments": {"account": mcp_account}},
            },
        ).json()

        assert account_api["account"] == "github:alice"
        assert account_api["github_login"] == "alice"
        assert account_api["exists"] is True
        assert account_api["balance_mrwk"] == "50"
        assert "50 MRWK" in account
        assert f"/proofs/{proof.hash}" in account
        assert 'href="https://github.com/alice">@alice</a>' in account
        assert "github:alice: 50 MRWK" in balance["result"]["content"][0]["text"]


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
    registered_wallet = json.loads(registered["result"]["content"][0]["text"])
    assert registered_wallet["address"].startswith("mrwk1")

    fetched = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_wallet",
                "arguments": {"address": registered_wallet["address"]},
            },
        },
    ).json()
    fetched_wallet = json.loads(fetched["result"]["content"][0]["text"])

    assert fetched_wallet["address"] == registered_wallet["address"]
    assert fetched_wallet["label"] == "MCP wallet"
    assert fetched_wallet["created_at"] == registered_wallet["created_at"]
