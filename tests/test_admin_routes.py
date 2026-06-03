from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.main import create_app
from app.models import WebhookEvent, utc_now


def test_admin_login_callback_and_logout_routes_remain_registered(sqlite_url: str) -> None:
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )

    login = client.get("/admin/login", follow_redirects=False)
    callback = client.get("/admin/callback?code=abc&state=xyz", follow_redirects=False)
    client.cookies.set("mrwk_admin", "admin-cookie")
    client.cookies.set("mrwk_user", "user-cookie")
    logout = client.post("/admin/logout", follow_redirects=False)

    assert login.status_code == 302
    assert login.headers["location"] == "/auth/github/login?next=/admin"
    assert callback.status_code == 302
    assert callback.headers["location"] == "/auth/github/callback?code=abc&state=xyz"
    assert logout.status_code == 303
    assert logout.headers["location"] == "/"
    set_cookie = logout.headers.get_list("set-cookie")
    assert any(cookie.startswith("mrwk_admin=") and "Max-Age=0" in cookie for cookie in set_cookie)
    assert any(cookie.startswith("mrwk_user=") and "Max-Age=0" in cookie for cookie in set_cookie)


def test_admin_page_requires_oauth_config_before_redirect(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/admin", follow_redirects=False)

    assert response.status_code == 503
    assert response.json()["detail"] == "GitHub OAuth is not configured"


def test_admin_webhook_page_allows_api_limit_cap(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")

    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        for index in range(120):
            session.add(
                WebhookEvent(
                    delivery_id=f"delivery-{index:03d}",
                    event_type="pull_request",
                    processed_status="missing_submitter",
                    payload_hash=f"hash-{index:03d}",
                    created_at=utc_now(),
                )
            )

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(
        "/admin?webhook_status=missing_submitter&webhook_limit=200",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )
    too_large = client.get(
        "/admin?webhook_status=missing_submitter&webhook_limit=201",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )

    assert response.status_code == 200
    assert response.text.count('data-label="Delivery"') == 120
    assert '<option value="200" selected>200</option>' in response.text
    assert too_large.status_code == 422


def test_admin_webhook_page_rejects_c1_control_status_before_normalizing(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(
        "/admin?webhook_status=%C2%85missing_submitter",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "webhook_status must not contain control characters"


def test_admin_webhook_page_rejects_repeated_status_filter(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(
        "/admin?webhook_status=missing_submitter&webhook_status=paid",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "webhook_status must be provided at most once"


@pytest.mark.parametrize(
    ("query", "detail"),
    [
        ("webhook_limit=01", "webhook_limit must be a canonical positive integer"),
        ("webhook_limit=%2B1", "webhook_limit must be a canonical positive integer"),
        ("webhook_limit=1.0", "webhook_limit must be a canonical positive integer"),
        ("webhook_limit=bad&webhook_limit=1", "webhook_limit must be provided at most once"),
        ("webhook_limit=%C2%851", "webhook_limit must not contain control characters"),
    ],
)
def test_admin_webhook_page_rejects_noncanonical_limit_values(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch, query: str, detail: str
) -> None:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(
        f"/admin?{query}",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == detail


@pytest.mark.parametrize("query", ["webhook_limit=1", "webhook_limit=200"])
def test_admin_webhook_page_keeps_valid_limit_values(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(
        f"/admin?{query}",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )

    assert response.status_code == 200


def test_admin_page_rejects_noncanonical_proposal_id_query(
    sqlite_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    for query in ("proposal_id=01", "proposal_id=%2B1", "proposal_id=1.0", "proposal_id=%C2%851"):
        response = client.get(
            f"/admin?{query}",
            headers={"x-mergework-admin-token": "admin-token-for-tests"},
        )
        assert response.status_code == 400
        assert response.json()["detail"].startswith("proposal_id must ")

    repeated = client.get(
        "/admin?proposal_id=bad&proposal_id=1",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )
    valid = client.get(
        "/admin?proposal_id=1",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )

    assert repeated.status_code == 400
    assert repeated.json()["detail"] == "proposal_id must be provided at most once"
    assert valid.status_code == 200
