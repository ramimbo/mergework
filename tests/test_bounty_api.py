"""Focused tests for bounty_api route extraction."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client():
    app = create_app()
    return TestClient(app)


class TestBountyListRoutes:
    """Bounty listing and search via REST API."""

    def test_list_bounties_returns_list(self, client):
        resp = client.get("/api/v1/bounties")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_bounties_filter_by_status(self, client):
        for status in ("open", "paid", "closed"):
            resp = client.get(f"/api/v1/bounties?status={status}")
            assert resp.status_code == 200

    def test_list_bounties_invalid_status_returns_400(self, client):
        resp = client.get("/api/v1/bounties?status=invalid")
        assert resp.status_code == 400
        assert "status must be one of" in resp.json()["detail"]

    def test_list_bounties_rejects_c1_control_status_before_normalizing(self, client):
        resp = client.get("/api/v1/bounties?status=%C2%85open")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "status must not contain control characters"

    def test_list_bounties_rejects_c1_control_sort_before_normalizing(self, client):
        resp = client.get("/api/v1/bounties?sort=%C2%85reward")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "sort must not contain control characters"

    def test_list_bounties_rejects_invalid_availability_filter(self, client):
        resp = client.get("/api/v1/bounties?availability=maybe")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "availability must be one of: all, effectively_open"

    def test_list_bounties_rejects_c1_control_availability_before_normalizing(self, client):
        resp = client.get("/api/v1/bounties?availability=%C2%85effectively_open")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "availability must not contain control characters"

    def test_list_bounties_with_query(self, client):
        resp = client.get("/api/v1/bounties?q=test")
        assert resp.status_code == 200

    def test_bounties_summary(self, client):
        resp = client.get("/api/v1/bounties/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "bounties_shown" in data


class TestBountyDetailRoute:
    """Single bounty detail retrieval."""

    def test_get_bounty_not_found(self, client):
        resp = client.get("/api/v1/bounties/999999")
        assert resp.status_code == 404

    def test_get_bounty_invalid_id(self, client):
        resp = client.get("/api/v1/bounties/-1")
        assert resp.status_code == 400

    def test_get_bounty_positive_id_required(self, client):
        resp = client.get("/api/v1/bounties/0")
        assert resp.status_code == 400


class TestBountyCreateRoute:
    """Bounty creation requires admin token."""

    def test_create_bounty_requires_auth(self, client):
        resp = client.post(
            "/api/v1/bounties",
            json={
                "repo": "test/repo",
                "issue_number": 1,
                "issue_url": "https://github.com/test/repo/issues/1",
                "title": "Test",
                "reward_mrwk": "50",
                "acceptance": "merge",
            },
        )
        assert resp.status_code == 401


class TestBountyPayRoute:
    """Bounty payment requires admin token."""

    def test_pay_bounty_requires_auth(self, client):
        resp = client.post(
            "/api/v1/bounties/1/pay",
            json={
                "to_account": "test",
                "submission_url": "https://github.com/test/repo/pull/1",
            },
        )
        assert resp.status_code == 401


def test_admin_webhook_events_api_rejects_c1_control_status(monkeypatch):
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-for-tests")
    client = TestClient(create_app(webhook_secret="secret"))

    resp = client.get(
        "/api/v1/admin/webhook-events?status=%C2%85paid",
        headers={"x-mergework-admin-token": "admin-token-for-tests"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "webhook_status must not contain control characters"


class TestBountyCloseRoute:
    """Bounty close requires admin token."""

    def test_close_bounty_requires_auth(self, client):
        resp = client.post("/api/v1/bounties/1/close", json={})
        assert resp.status_code == 401


class TestReconciliationRoute:
    """Payout reconciliation requires admin token."""

    def test_reconciliation_requires_auth(self, client):
        resp = client.get("/api/v1/reconciliation/payouts")
        assert resp.status_code == 401


class TestWebhookEventsRoute:
    """Webhook events list requires admin token."""

    def test_webhook_events_requires_auth(self, client):
        resp = client.get("/api/v1/admin/webhook-events")
        assert resp.status_code == 401
