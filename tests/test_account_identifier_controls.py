from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_account_api_rejects_c1_control_character_in_identifier(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/api/v1/accounts/foo%C2%85bar")

    assert response.status_code == 400
    assert response.json()["detail"] == "account must not contain control characters"


def test_account_page_rejects_c1_control_character_in_identifier(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/accounts/foo%C2%85bar")

    assert response.status_code == 400
    assert response.json()["detail"] == "account must not contain control characters"
