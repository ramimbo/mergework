from __future__ import annotations

from app.config import Settings, validate_deploy_settings


def _deploy_settings(**overrides: object) -> Settings:
    values = {
        "database_url": "sqlite:////tmp/mergework.sqlite3",
        "public_base_url": "https://mrwk.example.com",
        "github_webhook_secret": "abcdefghijklmnopqrstuvwxyz1234567890",
        "github_issue_token": "",
        "github_oauth_client_id": "oauth-client-id",
        "github_oauth_client_secret": "bcdefghijklmnopqrstuvwxyz1234567890a",
        "admin_logins": ("maintainer",),
        "admin_token": "cdefghijklmnopqrstuvwxyz1234567890ab",
        "cookie_secret": "defghijklmnopqrstuvwxyz1234567890abc",
        "github_accepted_labelers": ("maintainer",),
        "bounty_board_issue_number": 1,
    }
    values.update(overrides)
    return Settings(**values)


def test_validate_deploy_settings_ignores_empty_optional_github_issue_token() -> None:
    errors = validate_deploy_settings(_deploy_settings(github_issue_token=""))

    assert errors == []


def test_validate_deploy_settings_rejects_malformed_github_issue_token() -> None:
    errors = validate_deploy_settings(_deploy_settings(github_issue_token=" gh "))

    assert "MERGEWORK_GITHUB_ISSUE_TOKEN must not include leading or trailing whitespace" in errors


def test_validate_deploy_settings_rejects_duplicate_logins_case_insensitively() -> None:
    errors = validate_deploy_settings(
        _deploy_settings(
            admin_logins=("maintainer", "Maintainer"),
            github_accepted_labelers=("maintainer",),
        )
    )

    assert "MERGEWORK_ADMIN_LOGINS must not include duplicate logins" in errors


def test_validate_deploy_settings_rejects_whitespace_only_login_entries() -> None:
    errors = validate_deploy_settings(
        _deploy_settings(
            admin_logins=("maintainer", " "),
            github_accepted_labelers=("maintainer",),
        )
    )

    assert "MERGEWORK_ADMIN_LOGINS must not include empty entries" in errors
