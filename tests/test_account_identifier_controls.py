from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.parametrize("encoded_control", ("%C2%80", "%C2%85", "%C2%9F"))
def test_account_api_rejects_c1_control_character_in_identifier(
    sqlite_url: str, encoded_control: str
) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(f"/api/v1/accounts/foo{encoded_control}bar")

    assert response.status_code == 400
    assert response.json()["detail"] == "account must not contain control characters"


@pytest.mark.parametrize("encoded_control", ("%C2%80", "%C2%85", "%C2%9F"))
def test_account_page_rejects_c1_control_character_in_identifier(
    sqlite_url: str, encoded_control: str
) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(f"/accounts/foo{encoded_control}bar")

    assert response.status_code == 400
    assert response.json()["detail"] == "account must not contain control characters"
