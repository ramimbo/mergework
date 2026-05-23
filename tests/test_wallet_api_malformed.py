from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import _signed_value, create_app


def test_wallet_register_missing_public_key_returns_400(sqlite_url: str) -> None:
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        raise_server_exceptions=False,
    )

    response = client.post("/api/v1/wallets/register", json={"label": "Missing key"})

    assert response.status_code == 400
    assert response.json()["detail"] == "public_key_hex is required"


def test_wallet_transfer_bad_nonce_returns_400(sqlite_url: str) -> None:
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/api/v1/transfers",
        json={
            "from_address": "mrwk1" + "1" * 40,
            "to_address": "mrwk1" + "2" * 40,
            "amount_mrwk": "1",
            "nonce": "not-a-number",
            "signature_hex": "0" * 128,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "nonce must be an integer"


def test_wallet_link_non_object_body_returns_400(sqlite_url: str, monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
        raise_server_exceptions=False,
    )
    client.cookies.set("mrwk_user", _signed_value("alice", "test-cookie-secret"))

    response = client.post("/api/v1/wallets/link-github", json=["not", "an", "object"])

    assert response.status_code == 400
    assert response.json()["detail"] == "JSON body must be an object"


def test_github_claim_invalid_json_returns_400(sqlite_url: str, monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
        raise_server_exceptions=False,
    )
    client.cookies.set("mrwk_user", _signed_value("alice", "test-cookie-secret"))

    response = client.post(
        "/api/v1/github/claim",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid JSON body"
