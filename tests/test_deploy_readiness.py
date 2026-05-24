from __future__ import annotations

import os
import subprocess
import sys

from app.config import Settings, validate_deploy_settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "sqlite:////srv/mergework/data/mergework.sqlite3",
        "public_base_url": "https://staging.mrwk.example.test",
        "github_webhook_secret": "webhook-8efc3925bb8746b8a8fd3392c4c48e32",
        "github_oauth_client_id": "client-id",
        "github_oauth_client_secret": "oauth-7818e79f9d3a4a1d82ff0e1b9f0b8e42",
        "admin_logins": ("alice",),
        "admin_token": "admin-14dcaab83bb245f2bfb5d5c21a9bb55b",
        "cookie_secret": "cookie-27fd1c41324a4bdcb2e4014adc3a6108",
        "github_accepted_labelers": ("alice",),
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_deploy_readiness_accepts_strong_configuration() -> None:
    assert validate_deploy_settings(_settings()) == []


def test_deploy_readiness_rejects_missing_or_placeholder_secrets() -> None:
    errors = validate_deploy_settings(
        _settings(
            github_webhook_secret="change-me",
            admin_token="",
            cookie_secret="secret",
        )
    )

    assert "MERGEWORK_GITHUB_WEBHOOK_SECRET must be at least 32 characters" in errors
    assert "MERGEWORK_ADMIN_TOKEN is required" in errors
    assert "MERGEWORK_COOKIE_SECRET must be at least 32 characters" in errors


def test_deploy_readiness_rejects_low_diversity_secrets() -> None:
    errors = validate_deploy_settings(
        _settings(
            github_webhook_secret="a" * 48,
        )
    )

    assert "MERGEWORK_GITHUB_WEBHOOK_SECRET must look randomly generated" in errors


def test_deploy_readiness_requires_https_oauth_and_allowed_labelers() -> None:
    errors = validate_deploy_settings(
        _settings(
            public_base_url="http://mrwk.example.test",
            github_oauth_client_id="",
            github_accepted_labelers=(),
        )
    )

    assert "MERGEWORK_PUBLIC_BASE_URL must use https" in errors
    assert "MERGEWORK_GITHUB_OAUTH_CLIENT_ID is required" in errors
    assert "MERGEWORK_GITHUB_ACCEPTED_LABELERS must list maintainer logins" in errors


def test_deploy_readiness_rejects_public_base_url_path_query_or_fragment() -> None:
    path_errors = validate_deploy_settings(
        _settings(public_base_url="https://mrwk.example.test/app")
    )
    query_errors = validate_deploy_settings(
        _settings(public_base_url="https://mrwk.example.test?next=/admin")
    )
    fragment_errors = validate_deploy_settings(
        _settings(public_base_url="https://mrwk.example.test#callback")
    )

    assert "MERGEWORK_PUBLIC_BASE_URL must be an origin without a path" in path_errors
    assert "MERGEWORK_PUBLIC_BASE_URL must not include query or fragment" in query_errors
    assert "MERGEWORK_PUBLIC_BASE_URL must not include query or fragment" in fragment_errors


def test_deploy_readiness_script_runs_directly_from_source() -> None:
    env = {
        **os.environ,
        "MERGEWORK_DATABASE_URL": "sqlite:////srv/mergework/data/mergework.sqlite3",
        "MERGEWORK_PUBLIC_BASE_URL": "https://staging.mrwk.example.test",
        "MERGEWORK_GITHUB_WEBHOOK_SECRET": "webhook-8efc3925bb8746b8a8fd3392c4c48e32",
        "MERGEWORK_GITHUB_OAUTH_CLIENT_ID": "client-id",
        "MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET": "oauth-7818e79f9d3a4a1d82ff0e1b9f0b8e42",
        "MERGEWORK_ADMIN_LOGINS": "alice",
        "MERGEWORK_ADMIN_TOKEN": "admin-14dcaab83bb245f2bfb5d5c21a9bb55b",
        "MERGEWORK_COOKIE_SECRET": "cookie-27fd1c41324a4bdcb2e4014adc3a6108",
        "MERGEWORK_GITHUB_ACCEPTED_LABELERS": "alice",
    }

    result = subprocess.run(
        [sys.executable, "-S", "scripts/check_deploy_ready.py"],
        check=False,
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Deploy readiness check passed."
