from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.oauth_deploy_smoke import (
    is_healthy_oauth_route,
    validate_oauth_routes_registered,
)
from scripts.check_public_mrwk_links import analyze_probe_results, is_healthy_link

ROOT = Path(__file__).resolve().parents[1]


def test_validate_oauth_routes_registered(sqlite_url: str) -> None:
    errors = validate_oauth_routes_registered(sqlite_url)
    assert errors == []


def test_oauth_routes_return_fastapi_not_express(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    callback = client.get("/auth/github/callback", follow_redirects=False)
    assert callback.status_code == 422
    assert "Cannot GET" not in callback.text


def test_is_healthy_oauth_route_accepts_registered_fastapi_responses() -> None:
    assert is_healthy_oauth_route(503, '{"detail":"GitHub OAuth is not configured"}')
    assert is_healthy_oauth_route(422, '{"detail":[{"loc":["query","code"]}]}')
    assert not is_healthy_oauth_route(404, "Cannot GET /auth/github/callback")


def test_public_mrwk_links_fixture_lists_oauth_targets() -> None:
    payload = json.loads((ROOT / "fixtures" / "public_mrwk_links.json").read_text(encoding="utf-8"))
    assert {link["type"] for link in payload["links"]} >= {"oauth", "bounty", "proposal", "proof"}
    assert all("url" in link and "type" in link for link in payload["links"])


def test_analyze_probe_results_accepts_oauth_probe_rows() -> None:
    report = analyze_probe_results(
        [
            {
                "url": "https://mrwk.online/auth/github/login",
                "type": "oauth",
                "status_code": 503,
                "body": '{"detail":"GitHub OAuth is not configured"}',
            },
            {
                "url": "https://mrwk.online/auth/github/callback",
                "type": "oauth",
                "status_code": 422,
                "body": '{"detail":[{"loc":["query","code"]}]}',
            },
        ]
    )
    assert report["summary"]["unhealthy_links"] == 0


def test_check_deploy_ready_includes_oauth_route_gate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_DATABASE_URL", f"sqlite:///{tmp_path / 'deploy.sqlite3'}")
    monkeypatch.setenv("MERGEWORK_GITHUB_WEBHOOK_SECRET", "webhook-secret-32-characters-long")
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token-32-characters-long-ok")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "cookie-secret-32-characters-long")
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv(
        "MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET", "oauth-7818e79f9d3a4a1d82ff0e1b9f0b8e42"
    )
    monkeypatch.setenv("MERGEWORK_PUBLIC_BASE_URL", "https://mrwk.example.test")
    monkeypatch.setenv("MERGEWORK_ADMIN_LOGINS", "alice")
    monkeypatch.setenv("MERGEWORK_GITHUB_ACCEPTED_LABELERS", "alice")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_deploy_ready.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Deploy readiness check passed." in result.stdout


def test_is_healthy_link_delegates_oauth_type() -> None:
    assert is_healthy_link(422, '{"detail":[]}', link_type="oauth")
    assert not is_healthy_link(422, '{"detail":[]}', link_type="bounty")
