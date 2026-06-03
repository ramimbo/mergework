from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import close_bounty, create_bounty, ensure_genesis, pay_bounty
from app.main import create_app
from app.models import BountyAttempt, Proof
from app.serializers import public_utc_timestamp
from app.treasury import propose_treasury_action


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


@pytest.mark.parametrize(
    ("query", "expected_detail"),
    (
        ("%C2%8550", "limit must not contain control characters"),
        ("50.0", "limit must be a canonical positive integer"),
        ("%2B50", "limit must be a canonical positive integer"),
        ("050", "limit must be a canonical positive integer"),
    ),
)
def test_ledger_api_rejects_noncanonical_limit(
    sqlite_url: str, query: str, expected_detail: str
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(f"/api/v1/ledger?limit={query}")

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


def test_ledger_api_applies_default_limit_when_omitted(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/api/v1/ledger")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_ledger_api_rejects_repeated_limit_before_using_later_value(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/api/v1/ledger?limit=not-an-int&limit=1")

    assert response.status_code == 400
    assert response.json()["detail"] == "limit must be provided at most once"


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


def test_public_pages_clarify_current_transfer_paths(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    for path in ("/", "/docs"):
        response = client.get(path)
        assert response.status_code == 200
        assert "github:* balance claims into a linked wallet" in response.text
        assert "payouts to linked mrwk1 wallets" in response.text
        assert "signed wallet-to-wallet transfers between registered wallets" in response.text
        assert (
            "MergeWork does not currently operate a public BTC, USDC, fiat, "
            "bridge, exchange, or off-ramp."
        ) in response.text
        assert (
            "Future public snapshots, bridges, and onchain claims require separate "
            "maintainer/contributor discussion before implementation."
        ) in response.text


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
        base_url="http://mrwk.online",
    )
    api_client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="http://api.mrwk.online",
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
    assert public_page.headers["location"] == f"https://mrwk.online/bounties/{bounty.id}"
    assert public_api.status_code == 307
    assert public_api.headers["location"] == f"https://mrwk.online/api/v1/bounties/{bounty.id}"
    assert api_host.status_code == 307
    assert api_host.headers["location"] == f"https://api.mrwk.online/api/v1/bounties/{bounty.id}"


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


def test_mcp_tools_list_and_call(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
    assert tools["result"]["tools"][0]["name"] == "list_bounties"
    assert (
        "status, q, sort, limit, and availability filters"
        in tools["result"]["tools"][0]["description"]
    )
    submit_tool = next(
        tool for tool in tools["result"]["tools"] if tool["name"] == "submit_work_proof"
    )
    assert "bounty_id or issue_number" in submit_tool["description"]
    submit_schema = submit_tool["inputSchema"]
    assert submit_schema["additionalProperties"] is False
    assert submit_schema["properties"]["format"]["enum"] == ["text", "json"]
    assert submit_schema["properties"]["format"]["default"] == "text"
    assert submit_schema["properties"]["bounty_id"]["minimum"] == 1
    assert submit_schema["properties"]["issue_number"]["minimum"] == 1
    assert submit_schema["properties"]["repo"]["maxLength"] == 200
    assert submit_schema["not"] == {"required": ["bounty_id", "issue_number"]}
    bounty_tool = next(tool for tool in tools["result"]["tools"] if tool["name"] == "get_bounty")
    assert "accepted awards" in bounty_tool["description"]
    attempt_tool = next(
        tool for tool in tools["result"]["tools"] if tool["name"] == "list_bounty_attempts"
    )
    assert "active-attempt reservations" in attempt_tool["description"]

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
    assert "structuredContent" not in balance["result"]


def test_mcp_list_bounty_attempts_reports_active_and_expired(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    now = datetime.now(UTC)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Attempt reservations",
            reward_mrwk="250",
            max_awards=2,
            acceptance="Agents should inspect active attempts before opening work.",
        )
        session.add_all(
            [
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:alice",
                    source_url="https://github.com/ramimbo/mergework/pull/501",
                    status="active",
                    expires_at=now + timedelta(hours=1),
                    created_at=now - timedelta(minutes=2),
                    updated_at=now - timedelta(minutes=2),
                ),
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:bob",
                    source_url="https://github.com/ramimbo/mergework/pull/502",
                    status="active",
                    expires_at=now + timedelta(hours=2),
                    created_at=now - timedelta(minutes=1),
                    updated_at=now - timedelta(minutes=1),
                ),
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:carol",
                    source_url="https://github.com/ramimbo/mergework/pull/503",
                    status="active",
                    expires_at=now - timedelta(minutes=1),
                    created_at=now - timedelta(minutes=3),
                    updated_at=now - timedelta(minutes=3),
                ),
            ]
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    active_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "name": "list_bounty_attempts",
                "arguments": {"bounty_id": bounty.id},
            },
        },
    ).json()["result"]

    active_payload = active_result["structuredContent"]
    assert active_payload["bounty_id"] == bounty.id
    assert active_payload["issue_number"] == 321
    assert active_payload["warnings"] == ["bounty has 2 active attempts"]
    assert [attempt["submitter_account"] for attempt in active_payload["attempts"]] == [
        "github:bob",
        "github:alice",
    ]

    all_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 22,
            "method": "tools/call",
            "params": {
                "name": "list_bounty_attempts",
                "arguments": {"bounty_id": bounty.id, "include_expired": True, "limit": 3},
            },
        },
    ).json()["result"]

    all_payload = all_result["structuredContent"]
    assert [attempt["status"] for attempt in all_payload["attempts"]] == [
        "active",
        "active",
        "expired",
    ]


def test_mcp_list_bounty_attempts_accepts_issue_number_selector(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    now = datetime.now(UTC)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=322,
            issue_url="https://github.com/ramimbo/mergework/issues/322",
            title="Attempt lookup by issue",
            reward_mrwk="250",
            max_awards=2,
            acceptance="Agents can inspect attempts from a GitHub issue number.",
        )
        session.add(
            BountyAttempt(
                bounty_id=bounty.id,
                submitter_account="github:alice",
                source_url="https://github.com/ramimbo/mergework/pull/522",
                status="active",
                expires_at=now + timedelta(hours=1),
                created_at=now,
                updated_at=now,
            )
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 24,
            "method": "tools/call",
            "params": {
                "name": "list_bounty_attempts",
                "arguments": {"issue_number": 322, "repo": "RAMIMBO/MERGEWORK"},
            },
        },
    ).json()["result"]["structuredContent"]

    assert result["bounty_id"] == bounty_id
    assert result["issue_number"] == 322
    assert result["attempts"][0]["submitter_account"] == "github:alice"


def test_mcp_list_bounty_attempts_rejects_invalid_arguments(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Attempt reservations",
            reward_mrwk="250",
            acceptance="Agents should inspect attempts before opening work.",
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 23,
            "method": "tools/call",
            "params": {
                "name": "list_bounty_attempts",
                "arguments": {"bounty_id": bounty_id, "include_expired": "yes"},
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 23,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }


def test_mcp_list_bounties_filters_status_query_and_limit(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        open_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=284,
            issue_url="https://github.com/ramimbo/mergework/issues/284",
            title="Agent MCP workflow filters",
            reward_mrwk="100",
            acceptance="Agents should find open MCP bounty workflow work.",
        )
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=285,
            issue_url="https://github.com/ramimbo/mergework/issues/285",
            title="Paid proof lookup workflow",
            reward_mrwk="100",
            acceptance="Agents should inspect proof lookup behavior.",
        )
        closed_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=286,
            issue_url="https://github.com/ramimbo/mergework/issues/286",
            title="Closed MCP cleanup",
            reward_mrwk="100",
            acceptance="Closed bounty workflow inspection.",
        )
        pay_bounty(
            session,
            bounty_id=paid_bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/285",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        close_bounty(
            session,
            bounty_id=closed_bounty.id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/286#close",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    default_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_bounties", "arguments": {}},
        },
    ).json()
    default_payload = json.loads(default_result["result"]["content"][0]["text"])
    assert default_result["result"]["structuredContent"] == default_payload
    assert [item["id"] for item in default_payload] == [open_bounty.id]

    paid_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "list_bounties",
                "arguments": {"status": " Paid ", "q": "proof", "limit": 1},
            },
        },
    ).json()
    paid_payload = json.loads(paid_result["result"]["content"][0]["text"])
    assert paid_result["result"]["structuredContent"] == paid_payload
    assert [item["id"] for item in paid_payload] == [paid_bounty.id]
    assert paid_payload[0]["status"] == "paid"

    closed_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "list_bounties",
                "arguments": {"status": "closed", "q": "286"},
            },
        },
    ).json()
    closed_payload = json.loads(closed_result["result"]["content"][0]["text"])
    assert [item["id"] for item in closed_payload] == [closed_bounty.id]

    oversized_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "list_bounties",
                "arguments": {"q": "9" * 40},
            },
        },
    ).json()
    oversized_payload = json.loads(oversized_result["result"]["content"][0]["text"])
    assert oversized_payload == []

    digit_limit_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "list_bounties",
                "arguments": {"q": "9" * 5000},
            },
        },
    ).json()
    digit_limit_payload = json.loads(digit_limit_result["result"]["content"][0]["text"])
    assert digit_limit_payload == []


def test_mcp_list_bounties_honors_sort_argument(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        large_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=301,
            issue_url="https://github.com/ramimbo/mergework/issues/301",
            title="Large MCP bounty",
            reward_mrwk="100",
            acceptance="Agents can sort this higher reward bounty first.",
        )
        small_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=302,
            issue_url="https://github.com/ramimbo/mergework/issues/302",
            title="Small MCP bounty",
            reward_mrwk="25",
            acceptance="Agents can list this lower reward bounty.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "list_bounties", "arguments": {"sort": "reward"}},
        },
    ).json()

    payload = json.loads(result["result"]["content"][0]["text"])
    assert [item["id"] for item in payload] == [large_bounty.id, small_bounty.id]

    limited = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "list_bounties", "arguments": {"sort": "reward", "limit": 1}},
        },
    ).json()
    limited_payload = json.loads(limited["result"]["content"][0]["text"])
    assert [item["id"] for item in limited_payload] == [large_bounty.id]


def test_mcp_list_bounties_filters_effective_availability(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        open_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=303,
            issue_url="https://github.com/ramimbo/mergework/issues/303",
            title="MCP visible open bounty",
            reward_mrwk="100",
            acceptance="This row should remain visible.",
        )
        full_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=304,
            issue_url="https://github.com/ramimbo/mergework/issues/304",
            title="MCP raw-open full bounty",
            reward_mrwk="100",
            acceptance="This row should be hidden by effective availability.",
        )
        propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": full_bounty.id,
                "to_account": "github:alice",
                "submission_url": "https://github.com/ramimbo/mergework/pull/304",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "list_bounties",
                "arguments": {"availability": "effectively_open"},
            },
        },
    ).json()

    payload = json.loads(result["result"]["content"][0]["text"])
    assert [item["id"] for item in payload] == [open_bounty.id]


@pytest.mark.parametrize(
    ("arguments", "request_id"),
    [
        ({"status": "all"}, 31),
        ({"status": True}, 32),
        ({"q": 284}, 33),
        ({"limit": 0}, 34),
        ({"limit": 101}, 35),
        ({"sort": "invalid"}, 36),
        ({"availability": "maybe"}, 37),
    ],
)
def test_mcp_list_bounties_rejects_invalid_filters(
    sqlite_url: str, arguments: dict[str, object], request_id: int
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
            "params": {"name": "list_bounties", "arguments": arguments},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }


def test_mcp_rejects_malformed_requests_without_500(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    malformed = client.post(
        "/mcp",
        content="not-json",
        headers={"content-type": "application/json"},
    )
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


def test_mcp_get_bounty_can_include_accepted_awards(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=284,
            issue_url="https://github.com/ramimbo/mergework/issues/284",
            title="MCP accepted awards",
            reward_mrwk="75",
            acceptance="Agents should inspect accepted award proofs.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/284",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    default_result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_bounty", "arguments": {"id": bounty_id}},
        },
    ).json()
    default_payload = json.loads(default_result["result"]["content"][0]["text"])
    assert default_result["result"]["structuredContent"] == default_payload
    assert "awards" not in default_payload

    result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_bounty",
                "arguments": {"id": bounty_id, "include_awards": True},
            },
        },
    ).json()
    payload = json.loads(result["result"]["content"][0]["text"])
    assert result["result"]["structuredContent"] == payload
    assert payload["id"] == bounty_id
    assert payload["status"] == "paid"
    assert payload["awards_paid"] == 1
    assert payload["awards_remaining"] == 0
    assert payload["awards"] == [
        {
            "proof_hash": proof.hash,
            "proof_url": f"/proofs/{proof.hash}",
            "ledger_sequence": proof.ledger_sequence,
            "ledger_url": f"/ledger/{proof.ledger_sequence}",
            "account": "github:alice",
            "amount_mrwk": "75",
            "submission_url": "https://github.com/ramimbo/mergework/pull/284",
            "accepted_by": "maintainer",
            "created_at": public_utc_timestamp(proof.created_at),
        }
    ]


def test_mcp_get_bounty_accepts_issue_number_selector(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=286,
            issue_url="https://github.com/ramimbo/mergework/issues/286",
            title="MCP issue-number lookup",
            reward_mrwk="75",
            acceptance="Agents should inspect bounties from GitHub issue numbers.",
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_bounty",
                "arguments": {"issue_number": 286, "repo": "RAMIMBO/MERGEWORK"},
            },
        },
    ).json()

    payload = json.loads(result["result"]["content"][0]["text"])
    assert payload["id"] == bounty_id
    assert payload["issue_number"] == 286


def test_mcp_get_bounty_rejects_ambiguous_issue_number_selector(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=287,
            issue_url="https://github.com/ramimbo/mergework/issues/287",
            title="MCP issue lookup",
            reward_mrwk="75",
            acceptance="First repo issue.",
        )
        create_bounty(
            session,
            repo="other/repo",
            issue_number=287,
            issue_url="https://github.com/other/repo/issues/287",
            title="MCP issue lookup duplicate",
            reward_mrwk="75",
            acceptance="Second repo issue.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_bounty", "arguments": {"issue_number": 287}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }


def test_mcp_get_bounty_rejects_mixed_selectors(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=288,
            issue_url="https://github.com/ramimbo/mergework/issues/288",
            title="MCP mixed selector validation",
            reward_mrwk="75",
            acceptance="Agents should choose one selector.",
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "get_bounty",
                "arguments": {
                    "id": bounty_id,
                    "issue_number": 288,
                    "repo": "ramimbo/mergework",
                },
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 5,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }


def test_mcp_get_bounty_skips_malformed_award_proof_payloads(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=285,
            issue_url="https://github.com/ramimbo/mergework/issues/285",
            title="MCP malformed award proof",
            reward_mrwk="75",
            acceptance="Malformed stored proof JSON should not break MCP bounty inspection.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/285",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        proof_row = session.get(Proof, proof.hash)
        assert proof_row is not None
        proof_row.public_json = "{"
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_bounty",
                "arguments": {"id": bounty_id, "include_awards": True},
            },
        },
    ).json()

    payload = json.loads(result["result"]["content"][0]["text"])
    assert payload["id"] == bounty_id
    assert payload["awards_paid"] == 1
    assert payload["awards"] == []


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


@pytest.mark.parametrize("include_awards", ["true", 1, []])
def test_mcp_get_bounty_rejects_non_boolean_include_awards(
    sqlite_url: str, include_awards: object
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=104,
            issue_url="https://github.com/ramimbo/mergework/issues/104",
            title="MCP awards flag validation",
            reward_mrwk="75",
            acceptance="MCP tools should reject non-boolean include_awards.",
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "get_bounty",
                "arguments": {"id": bounty_id, "include_awards": include_awards},
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 12,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }


@pytest.mark.parametrize("bounty_id", [0, -1])
def test_mcp_get_bounty_rejects_non_positive_id(sqlite_url: str, bounty_id: int) -> None:
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


def test_mcp_get_wallet_returns_not_found_for_unregistered_wallet(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    unregistered_wallet = "mrwk1" + "a" * 40

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {"name": "get_wallet", "arguments": {"address": unregistered_wallet}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 13,
        "result": {"content": [{"type": "text", "text": "wallet not found"}]},
    }


@pytest.mark.parametrize(
    ("tool_name", "arguments", "request_id"),
    [
        ("get_bounty", {"id": "9" * 40}, 16),
        ("get_bounty", {"id": 2**63}, 17),
        ("get_ledger_entry", {"sequence": "9" * 40}, 18),
        ("submit_work_proof", {"bounty_id": "9" * 40}, 19),
        ("submit_work_proof", {"issue_number": "9" * 40}, 20),
    ],
)
def test_mcp_rejects_oversized_integer_arguments_without_500(
    sqlite_url: str, tool_name: str, arguments: dict[str, object], request_id: int
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        raise_server_exceptions=False,
    )

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


@pytest.mark.parametrize(
    ("tool_name", "arguments", "request_id"),
    [
        ("list_bounties", {"limit": "03"}, 21),
        ("list_bounties", {"limit": "+3"}, 22),
        ("list_bounties", {"limit": " 3"}, 23),
        ("list_bounties", {"limit": "3 "}, 24),
        ("get_bounty", {"id": "099"}, 25),
        ("get_bounty", {"id": "+99"}, 26),
        ("get_bounty", {"id": "99 "}, 27),
        ("get_ledger_entry", {"sequence": "01"}, 28),
        ("submit_work_proof", {"bounty_id": "099"}, 29),
        ("submit_work_proof", {"issue_number": "0656"}, 30),
    ],
)
def test_mcp_rejects_noncanonical_integer_string_arguments(
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


def test_mcp_accepts_canonical_integer_string_arguments(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=656,
            issue_url="https://github.com/ramimbo/mergework/issues/656",
            title="MCP canonical integer args",
            reward_mrwk="75",
            acceptance="MCP should keep canonical numeric string compatibility.",
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 29,
            "method": "tools/call",
            "params": {"name": "get_bounty", "arguments": {"id": str(bounty_id)}},
        },
    )

    payload = json.loads(response.json()["result"]["content"][0]["text"])
    assert payload["id"] == bounty_id


@pytest.mark.parametrize(
    ("tool_name", "arguments", "request_id"),
    [
        ("get_balance", {"account": True}, 13),
        ("get_balance", {"account": 123}, 14),
        ("get_balance", {"account": ""}, 15),
        ("get_wallet", {"address": 123}, 16),
        ("get_proof", {"hash": 123}, 17),
        ("get_wallet", {"address": "not-a-wallet"}, 18),
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
    assert result["result"]["structuredContent"] == payload

    assert payload["sequence"] == ledger_sequence
    assert payload["proof_hash"] == proof_hash


def test_mcp_get_ledger_entry_rejects_non_positive_sequence(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_ledger_entry", "arguments": {"sequence": 0}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }


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
    assert result["result"]["structuredContent"] == payload
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


def test_mcp_get_proof_reports_malformed_payload(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=37,
            issue_url="https://github.com/ramimbo/mergework/issues/37",
            title="MCP malformed proof lookup",
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
        proof_row = session.get(Proof, proof.hash)
        assert proof_row is not None
        proof_row.public_json = "{"

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_proof", "arguments": {"hash": proof.hash}},
        },
    ).json()

    assert result["result"]["content"][0]["text"] == "invalid proof payload"


def test_public_proof_api_reports_malformed_payload(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=38,
            issue_url="https://github.com/ramimbo/mergework/issues/38",
            title="Proof payload lookup",
            reward_mrwk="25",
            acceptance="Public proof lookups should return bounded errors.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/38",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        proof_row = session.get(Proof, proof.hash)
        assert proof_row is not None
        proof_row.public_json = "{"

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(f"/api/v1/proofs/{proof.hash}")

    assert response.status_code == 500
    assert response.json()["detail"] == "invalid proof payload"


def test_mcp_get_proof_rejects_malformed_hash(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_proof", "arguments": {"hash": "not-a-proof-hash"}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }


def test_mcp_submit_work_proof_returns_bounty_specific_guidance(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=284,
            issue_url="https://github.com/ramimbo/mergework/issues/284",
            title="Agent MCP bounty workflow",
            reward_mrwk="100",
            max_awards=3,
            acceptance="Improve MCP behavior with focused tests.",
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    by_issue = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "submit_work_proof",
                "arguments": {"issue_number": 284},
            },
        },
    ).json()
    text = by_issue["result"]["content"][0]["text"]

    assert "Bounty #284: Agent MCP bounty workflow" in text
    assert f"Internal bounty id: {bounty_id}" in text
    assert "Repository: ramimbo/mergework" in text
    assert "Issue: https://github.com/ramimbo/mergework/issues/284" in text
    assert "Status: open (open for submissions); awards remaining: 3 of 3" in text
    assert "Reward: 100 MRWK per accepted award" in text
    assert "Acceptance: Improve MCP behavior with focused tests." in text
    assert "/claim" in text
    assert "Do not include private keys" in text

    by_id = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "submit_work_proof",
                "arguments": {"bounty_id": bounty_id},
            },
        },
    ).json()

    assert by_id["result"]["content"][0]["text"] == text


def test_mcp_submit_work_proof_keeps_generic_guidance(sqlite_url: str) -> None:
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
            "params": {"name": "submit_work_proof", "arguments": {}},
        },
    ).json()

    assert result["result"]["content"][0]["text"] == (
        "Open a focused PR or issue, reference the MRWK bounty, include test evidence, "
        "and wait for a maintainer to apply mrwk:accepted."
    )


def test_mcp_submit_work_proof_returns_structured_bounty_guidance(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=315,
            issue_url="https://github.com/ramimbo/mergework/issues/315",
            title="Structured MCP work-proof guidance",
            reward_mrwk="100",
            max_awards=3,
            acceptance="Return machine-readable work-proof guidance.",
        )
        bounty_id = bounty.id

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    result = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "submit_work_proof",
                "arguments": {"bounty_id": bounty_id, "format": "json"},
            },
        },
    ).json()["result"]

    structured = result["structuredContent"]
    assert json.loads(result["content"][0]["text"]) == structured
    assert structured["bounty_id"] == bounty_id
    assert structured["issue_number"] == 315
    assert structured["status"] == "open"
    assert structured["availability"] == "open_for_submissions"
    assert structured["can_submit"] is True
    assert structured["availability_warnings"] == []
    assert structured["awards_remaining"] == 3
    assert structured["max_awards"] == 3
    assert structured["awards_paid"] == 0
    assert structured["reward_mrwk"] == "100"
    assert structured["available_mrwk"] == "300"
    assert structured["repository"] == "ramimbo/mergework"
    assert structured["issue_url"] == "https://github.com/ramimbo/mergework/issues/315"
    assert structured["title"] == "Structured MCP work-proof guidance"
    assert structured["acceptance"] == "Return machine-readable work-proof guidance."
    assert "/claim" in structured["submission_format"]
    requirements = structured["submission_requirements"]
    assert requirements["reference_formats"] == ["Bounty #315", "Refs #315"]
    assert requirements["claim_command"] == "/claim"
    assert requirements["attempt_endpoint"] == f"/api/v1/bounties/{bounty_id}/attempts"
    assert requirements["acceptance_trigger"] == ("maintainer_mrwk_accepted_label_or_admin_payout")
    assert "focused PR, issue, report, or evidence URL" in requirements["evidence_required"]
    assert "price claims" in requirements["public_metadata_must_avoid"]
    assert [action["id"] for action in requirements["next_actions"]] == [
        "confirm_award_slot",
        "check_duplicate_scope",
        "keep_scope_focused",
        "include_bounty_reference",
        "include_review_evidence",
        "wait_for_maintainer_acceptance",
    ]
    assert "private keys" in structured["safety_rules"][0]


def test_mcp_submit_work_proof_returns_structured_generic_guidance(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "submit_work_proof", "arguments": {"format": "json"}},
        },
    )
    response_result = response.json()["result"]
    result = response_result["structuredContent"]

    assert result == {
        "bounty_id": None,
        "issue_number": None,
        "status": "generic_guidance",
        "availability": "unknown_without_bounty",
        "can_submit": None,
        "availability_warnings": [],
        "awards_remaining": None,
        "max_awards": None,
        "awards_paid": None,
        "reward_mrwk": None,
        "available_mrwk": None,
        "repository": None,
        "issue_url": None,
        "title": None,
        "acceptance": None,
        "submission_format": (
            "Open a focused PR or issue, reference the MRWK bounty, include test "
            "evidence, and wait for a maintainer to apply mrwk:accepted."
        ),
        "submission_requirements": {
            "submission_mode": "pr_or_evidence",
            "submission_url_kind": "github_pr_or_public_evidence_url",
            "expected_artifact": "focused PR, issue, report, or evidence URL",
            "attempt_endpoint_applicability": "recommended_before_submission",
            "reference_formats": ["Bounty #<issue_number>", "Refs #<issue_number>"],
            "claim_command": "/claim",
            "attempt_endpoint": "/api/v1/bounties/<bounty_id>/attempts",
            "evidence_required": [
                "focused PR, issue, report, or evidence URL",
                "short verification summary",
                "tests, command output, screenshots, or reproduction steps when relevant",
            ],
            "acceptance_trigger": "maintainer_mrwk_accepted_label_or_admin_payout",
            "public_metadata_must_avoid": [
                "private keys",
                "seed material",
                "secrets",
                "deployment credentials",
                "private vulnerability details",
                "price claims",
            ],
            "next_actions": [
                {
                    "id": "select_bounty",
                    "required": True,
                    "text": "Select a concrete open bounty before submitting work proof.",
                },
                {
                    "id": "check_duplicate_scope",
                    "required": True,
                    "text": (
                        "Confirm no active claim or duplicate PR already covers the same scope."
                    ),
                },
                {
                    "id": "keep_scope_focused",
                    "required": True,
                    "text": "Keep changes directly tied to one bounty issue.",
                },
                {
                    "id": "include_bounty_reference",
                    "required": True,
                    "text": (
                        "Include Bounty #<issue_number> or Refs #<issue_number> in the submission."
                    ),
                },
                {
                    "id": "include_review_evidence",
                    "required": True,
                    "text": "Include reviewable validation evidence before claiming.",
                },
                {
                    "id": "wait_for_maintainer_acceptance",
                    "required": True,
                    "text": (
                        "Payment requires mrwk:accepted or an admin payout; merge or CI "
                        "alone is not acceptance."
                    ),
                },
            ],
        },
        "safety_rules": [
            "Do not include private keys, seed material, secrets, deployment "
            "credentials, private vulnerability details, or price claims."
        ],
    }
    assert json.loads(response_result["content"][0]["text"]) == result


def test_mcp_submit_work_proof_structures_terminal_bounty_availability(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        paid_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=316,
            issue_url="https://github.com/ramimbo/mergework/issues/316",
            title="Paid structured guidance",
            reward_mrwk="100",
            acceptance="Expose paid guidance state.",
        )
        closed_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=317,
            issue_url="https://github.com/ramimbo/mergework/issues/317",
            title="Closed structured guidance",
            reward_mrwk="100",
            max_awards=2,
            acceptance="Expose closed guidance state.",
        )
        paid_bounty_id = paid_bounty.id
        closed_bounty_id = closed_bounty.id
        pay_bounty(
            session,
            bounty_id=paid_bounty_id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/316",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        close_bounty(
            session,
            bounty_id=closed_bounty_id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/317#close",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    def structured_guidance(bounty_id: int, request_id: int) -> dict[str, object]:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": "submit_work_proof",
                    "arguments": {"bounty_id": bounty_id, "format": "json"},
                },
            },
        ).json()["result"]
        assert json.loads(response["content"][0]["text"]) == response["structuredContent"]
        return response["structuredContent"]

    paid = structured_guidance(paid_bounty_id, 1)
    closed = structured_guidance(closed_bounty_id, 2)

    assert paid["status"] == "paid"
    assert paid["availability"] == "not_currently_open"
    assert paid["can_submit"] is False
    assert paid["availability_warnings"] == [
        "bounty is paid",
        "bounty has no award slots remaining",
    ]
    assert paid["submission_requirements"]["next_actions"][0]["id"] == "choose_open_bounty"
    assert closed["status"] == "closed"
    assert closed["availability"] == "not_currently_open"
    assert closed["can_submit"] is False
    assert closed["availability_warnings"] == [
        "bounty is closed",
        "bounty has no award slots remaining",
    ]
    assert closed["submission_requirements"]["next_actions"][0]["id"] == "choose_open_bounty"


def test_mcp_submit_work_proof_reports_unknown_bounty(sqlite_url: str) -> None:
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
            "params": {
                "name": "submit_work_proof",
                "arguments": {"issue_number": 999},
            },
        },
    ).json()

    assert result["result"]["content"][0]["text"] == "bounty not found"


def test_mcp_submit_work_proof_scopes_issue_number_by_repo(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    with session_scope(sqlite_url) as session:
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=284,
            issue_url="https://github.com/ramimbo/mergework/issues/284",
            title="First bounty",
            reward_mrwk="100",
            acceptance="First acceptance.",
        )
        target = create_bounty(
            session,
            repo="example/mergework",
            issue_number=284,
            issue_url="https://github.com/example/mergework/issues/284",
            title="Second bounty",
            reward_mrwk="250",
            acceptance="Second acceptance.",
        )
        target_id = target.id

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 28,
            "method": "tools/call",
            "params": {
                "name": "submit_work_proof",
                "arguments": {
                    "issue_number": 284,
                    "repo": "Example/MergeWork",
                    "format": "json",
                },
            },
        },
    )

    result = response.json()["result"]
    structured = result["structuredContent"]
    assert json.loads(result["content"][0]["text"]) == structured
    assert structured["bounty_id"] == target_id
    assert structured["repository"] == "example/mergework"
    assert structured["title"] == "Second bounty"
    assert structured["reward_mrwk"] == "250"
    assert structured["acceptance"] == "Second acceptance."


@pytest.mark.parametrize(
    ("arguments", "request_id"),
    [
        ({"bounty_id": 0}, 21),
        ({"bounty_id": True}, 22),
        ({"issue_number": 0}, 23),
        ({"issue_number": 1.5}, 24),
        ({"bounty_id": 1, "issue_number": 1}, 25),
        ({"format": "xml"}, 26),
        ({"format": 1}, 27),
        ({"repo": "ramimbo/mergework"}, 29),
        ({"bounty_id": 1, "repo": "ramimbo/mergework"}, 30),
        ({"issue_number": 1, "repo": 1}, 31),
        ({"issue_number": 1, "repo": "a" * 201}, 32),
    ],
)
def test_mcp_submit_work_proof_rejects_invalid_bounty_selectors(
    sqlite_url: str, arguments: dict[str, object], request_id: int
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
            "params": {"name": "submit_work_proof", "arguments": arguments},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }


def test_mcp_submit_work_proof_rejects_ambiguous_issue_number(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=284,
            issue_url="https://github.com/ramimbo/mergework/issues/284",
            title="First bounty",
            reward_mrwk="100",
            acceptance="First acceptance.",
        )
        create_bounty(
            session,
            repo="example/mergework",
            issue_number=284,
            issue_url="https://github.com/example/mergework/issues/284",
            title="Second bounty",
            reward_mrwk="100",
            acceptance="Second acceptance.",
        )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 26,
            "method": "tools/call",
            "params": {"name": "submit_work_proof", "arguments": {"issue_number": 284}},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 26,
        "error": {"code": -32602, "message": "invalid tool arguments"},
    }


def test_host_specific_homepages(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    lab = client.get("/", headers={"host": "ltclab.site"}).text
    mrwk = client.get("/", headers={"host": "mrwk.online"}).text
    legacy_mrwk = client.get("/", headers={"host": "mrwk.ltclab.site"}).text

    assert "LTC Lab" in lab
    assert "MRWK from LTC Lab" in lab
    assert "https://api.mrwk.online" in lab
    assert "https://mcp.mrwk.online" in lab
    assert "Open-source work, recorded as MRWK" in mrwk
    assert "MRWK from LTC Lab" in mrwk
    assert "https://api.mrwk.online" in mrwk
    assert "https://mcp.mrwk.online" in mrwk
    assert "Open-source work, recorded as MRWK" in legacy_mrwk


def test_docs_page_lists_live_ltclab_urls(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    docs = client.get("/docs").text
    api_docs = client.get("/api/docs").text

    assert "https://ltclab.site" in docs
    assert "https://mrwk.online" in docs
    assert "https://api.mrwk.online" in docs
    assert "https://mcp.mrwk.online" in docs
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
    accepted_work_api = client.get("/api/v1/accounts/github:alice/accepted-work").json()
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
    assert accepted_work_api["account"] == "github:alice"
    assert accepted_work_api["summary"] == {
        "accepted_awards": 1,
        "accepted_mrwk": "25",
        "latest_ledger_sequence": 3,
        "latest_submission_url": "https://github.com/ramimbo/mergework/pull/3",
        "latest_proof_hash": proof.hash,
        "latest_proof_url": f"/proofs/{proof.hash}",
        "latest_proof_public_url": f"https://mrwk.online/proofs/{proof.hash}",
    }
    assert accepted_work_api["summary"] == account_api["accepted_work"]
    accepted_work = accepted_work_api["accepted_work"]
    assert len(accepted_work) == 1
    assert accepted_work[0]["created_at"]
    assert accepted_work[0] | {"created_at": "<checked>"} == {
        "ledger_sequence": 3,
        "ledger_url": "/ledger/3",
        "ledger_public_url": "https://mrwk.online/ledger/3",
        "proof_hash": proof.hash,
        "proof_url": f"/proofs/{proof.hash}",
        "proof_public_url": f"https://mrwk.online/proofs/{proof.hash}",
        "amount_mrwk": "25",
        "submission_url": "https://github.com/ramimbo/mergework/pull/3",
        "issue_url": "https://github.com/ramimbo/mergework/issues/2",
        "repo": "ramimbo/mergework",
        "issue_number": 2,
        "bounty_id": bounty.id,
        "bounty_url": f"/bounties/{bounty.id}",
        "bounty_public_url": f"https://mrwk.online/bounties/{bounty.id}",
        "accepted_by": "maintainer",
        "created_at": "<checked>",
    }
    assert "Claim GitHub balances from /me" in account
    assert 'href="https://github.com/alice">@alice</a>' in account
    assert "Accepted work summary" in account
    assert "1</strong>\n      <span>accepted awards</span>" in account
    assert "25 MRWK</strong>\n      <span>accepted MRWK</span>" in account
    assert f'href="/proofs/{proof.hash}"' in account
    assert 'href="https://github.com/ramimbo/mergework/pull/3"' in account
    assert 'via <a href="/ledger/3">#3</a>' in account
    assert "Accepted work" in account
    assert "Proof-backed bounty payments made to this account." in account
    assert f'href="/bounties/{bounty.id}">Bounty #{bounty.id}</a>' in account
    assert 'href="https://github.com/ramimbo/mergework/issues/2"' in account
    assert 'href="/accounts/reserve:bounty:1"' in account
    assert 'href="/accounts/github:alice"' in account


def test_account_api_keeps_schema_when_accepted_work_proof_is_malformed(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=42,
            issue_url="https://github.com/ramimbo/mergework/issues/42",
            title="Malformed accepted-work proof",
            reward_mrwk="25",
            acceptance="Accepted label",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/42",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        proof_row = session.get(Proof, proof.hash)
        assert proof_row is not None
        proof_row.public_json = "{"

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/api/v1/accounts/github:alice")
    account_page = client.get("/accounts/github:alice")

    assert response.status_code == 200
    assert account_page.status_code == 200
    account_api = response.json()
    assert account_api["balance_mrwk"] == "25"
    assert account_api["accepted_work"] == {
        "accepted_awards": 0,
        "accepted_mrwk": "0",
        "latest_ledger_sequence": None,
        "latest_submission_url": None,
        "latest_proof_hash": None,
        "latest_proof_url": None,
        "latest_proof_public_url": None,
    }


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

    padded_api = client.get("/api/v1/accounts/%20GitHub:Alice%20")
    padded_page = client.get("/accounts/%20GitHub:Alice%20")
    padded_mcp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_balance", "arguments": {"account": " GitHub:Alice "}},
        },
    ).json()

    assert padded_api.status_code == 400
    assert padded_api.json()["detail"] == "account must not contain leading or trailing whitespace"
    assert padded_page.status_code == 400
    assert padded_page.json()["detail"] == "account must not contain leading or trailing whitespace"
    assert "github:alice: 50 MRWK" in padded_mcp["result"]["content"][0]["text"]


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
    assert registered["result"]["structuredContent"] == registered_wallet
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
    assert fetched["result"]["structuredContent"] == fetched_wallet

    assert fetched_wallet["address"] == registered_wallet["address"]
    assert fetched_wallet["label"] == "MCP wallet"
    assert fetched_wallet["created_at"] == registered_wallet["created_at"]
