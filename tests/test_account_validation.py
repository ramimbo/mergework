from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis
from app.main import create_app


def _setup_app(sqlite_url: str) -> TestClient:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
    return TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))


def test_api_account_rejects_empty(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.get("/api/v1/accounts/%20%20%20")
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_api_account_rejects_null_byte(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.get("/api/v1/accounts/test%00account")
    assert resp.status_code == 400
    assert "control character" in resp.json()["detail"].lower()


def test_api_account_rejects_tab(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.get("/api/v1/accounts/test%09account")
    assert resp.status_code == 400
    assert "control character" in resp.json()["detail"].lower()


@pytest.mark.parametrize("encoded_control", ["%C2%80", "%C2%85", "%C2%9F"])
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/accounts/{encoded_control}github:alice",
        "/api/v1/accounts/github:alice{encoded_control}",
        "/api/v1/accounts/{encoded_control}github:alice/accepted-work",
        "/accounts/{encoded_control}github:alice",
    ],
)
def test_account_views_reject_c1_controls_before_normalizing(
    sqlite_url: str, path: str, encoded_control: str
) -> None:
    client = _setup_app(sqlite_url)

    resp = client.get(path.format(encoded_control=encoded_control))

    assert resp.status_code == 400
    assert resp.json()["detail"] == "account must not contain control characters"


def test_api_account_accepts_valid(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.get("/api/v1/accounts/github:alice")
    assert resp.status_code == 200
    assert resp.json()["account"] == "github:alice"


def test_api_account_rejects_empty_github_login(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.get("/api/v1/accounts/github:%20")
    assert resp.status_code == 400
    assert "github login" in resp.json()["detail"].lower()


def test_mcp_get_balance_rejects_empty_account(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_balance", "arguments": {"account": ""}},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32602


def test_mcp_get_balance_rejects_empty_github_login(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_balance", "arguments": {"account": "github: "}},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32602


def test_mcp_get_balance_rejects_null_byte_account(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_balance", "arguments": {"account": "test\x00account"}},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32602


def test_mcp_get_balance_rejects_c1_control_account(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_balance", "arguments": {"account": "\x85github:alice"}},
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32602


def test_account_page_rejects_null_byte(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.get("/accounts/test%00account")
    assert resp.status_code == 400


def test_account_page_rejects_empty_github_login(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.get("/accounts/github:%20")
    assert resp.status_code == 400


def test_account_views_normalize_treasury_account(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)

    api_resp = client.get("/api/v1/accounts/Treasury:MRWK")
    page_resp = client.get("/accounts/Treasury:MRWK")
    mcp_resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_balance", "arguments": {"account": "Treasury:MRWK"}},
        },
    )

    assert api_resp.status_code == 200
    assert api_resp.json()["account"] == "treasury:mrwk"
    assert api_resp.json()["exists"] is True
    assert page_resp.status_code == 200
    assert mcp_resp.status_code == 200
    assert mcp_resp.json()["result"]["content"][0]["text"].startswith("treasury:mrwk: ")


@pytest.mark.parametrize("account", ["treasury:", "treasury:ops"])
def test_account_views_reject_malformed_treasury_accounts(sqlite_url: str, account: str) -> None:
    client = _setup_app(sqlite_url)

    api_resp = client.get(f"/api/v1/accounts/{account}")
    page_resp = client.get(f"/accounts/{account}")
    mcp_resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_balance", "arguments": {"account": account}},
        },
    )

    assert api_resp.status_code == 400
    assert page_resp.status_code == 400
    assert mcp_resp.status_code == 200
    assert mcp_resp.json()["error"]["code"] == -32602


def test_account_views_accept_valid_reserve_bounty_account(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=122,
            issue_url="https://github.com/ramimbo/mergework/issues/122",
            title="Useful small fixes",
            reward_mrwk="50",
            acceptance="Accepted focused fix.",
        )
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    api_resp = client.get("/api/v1/accounts/reserve:bounty:1")
    page_resp = client.get("/accounts/reserve:bounty:1")
    mcp_resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_balance", "arguments": {"account": "Reserve:Bounty:001"}},
        },
    )

    assert api_resp.status_code == 200
    assert api_resp.json()["account"] == "reserve:bounty:1"
    assert api_resp.json()["exists"] is True
    assert page_resp.status_code == 200
    assert mcp_resp.status_code == 200
    assert mcp_resp.json()["result"]["content"][0]["text"] == "reserve:bounty:1: 50 MRWK"


def test_account_views_accept_valid_mrwk_wallet_account(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    account = "mrwk1" + ("0" * 40)

    api_resp = client.get(f"/api/v1/accounts/{account}")
    page_resp = client.get(f"/accounts/{account}")
    mcp_resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_balance", "arguments": {"account": account.upper()}},
        },
    )

    assert api_resp.status_code == 200
    assert api_resp.json()["account"] == account
    assert page_resp.status_code == 200
    assert mcp_resp.status_code == 200
    assert mcp_resp.json()["result"]["content"][0]["text"] == f"{account}: 0 MRWK"


@pytest.mark.parametrize(
    "account",
    [
        "mrwk1bad",
        "mrwk1" + ("0" * 39),
        "mrwk1" + ("g" * 40),
    ],
)
def test_account_views_reject_malformed_mrwk_wallet_accounts(sqlite_url: str, account: str) -> None:
    client = _setup_app(sqlite_url)

    api_resp = client.get(f"/api/v1/accounts/{account}")
    page_resp = client.get(f"/accounts/{account}")
    mcp_resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_balance", "arguments": {"account": account}},
        },
    )

    assert api_resp.status_code == 400
    assert api_resp.json()["detail"] == "invalid MRWK wallet address"
    assert page_resp.status_code == 400
    assert mcp_resp.status_code == 200
    assert mcp_resp.json()["error"]["code"] == -32602


@pytest.mark.parametrize(
    "account",
    [
        "reserve:",
        "reserve:wallet:1",
        "reserve:bounty:",
        "reserve:bounty:0",
        "reserve:bounty:-1",
        "reserve:bounty:not-a-number",
        "reserve:bounty:" + "9" * 5000,
    ],
)
def test_account_views_reject_malformed_reserve_accounts(sqlite_url: str, account: str) -> None:
    client = _setup_app(sqlite_url)

    api_resp = client.get(f"/api/v1/accounts/{account}")
    page_resp = client.get(f"/accounts/{account}")
    mcp_resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_balance", "arguments": {"account": account}},
        },
    )

    assert api_resp.status_code == 400
    assert page_resp.status_code == 400
    assert mcp_resp.status_code == 200
    assert mcp_resp.json()["error"]["code"] == -32602
