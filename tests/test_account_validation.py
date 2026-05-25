from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import ensure_genesis
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

def test_api_account_rejects_invalid_wallet_address(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.get("/api/v1/accounts/mrwk1xyz")
    assert resp.status_code == 400
    assert "invalid mrwk wallet address" in resp.json()["detail"].lower()


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

def test_mcp_get_balance_rejects_invalid_wallet_account(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_balance", "arguments": {"account": "mrwk1xyz"}},
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


def test_account_page_rejects_null_byte(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.get("/accounts/test%00account")
    assert resp.status_code == 400


def test_account_page_rejects_empty_github_login(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.get("/accounts/github:%20")
    assert resp.status_code == 400

def test_account_page_rejects_invalid_wallet_address(sqlite_url: str) -> None:
    client = _setup_app(sqlite_url)
    resp = client.get("/accounts/mrwk1xyz")
    assert resp.status_code == 400
