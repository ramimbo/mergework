from __future__ import annotations

import os
import subprocess
import sys

import pytest

from app.config import Settings, get_settings, validate_deploy_settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "sqlite:////srv/mergework/data/mergework.sqlite3",
        "public_base_url": "https://staging.mrwk.example.test",
        "github_webhook_secret": "webhook-8efc3925bb8746b8a8fd3392c4c48e32",
        "github_issue_token": "",
        "github_oauth_client_id": "client-id",
        "github_oauth_client_secret": "oauth-7818e79f9d3a4a1d82ff0e1b9f0b8e42",
        "admin_logins": ("alice",),
        "admin_token": "admin-14dcaab83bb245f2bfb5d5c21a9bb55b",
        "cookie_secret": "cookie-27fd1c41324a4bdcb2e4014adc3a6108",
        "github_accepted_labelers": ("alice",),
        "bounty_board_issue_number": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _deploy_ready_env(**overrides: str) -> dict[str, str]:
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("MERGEWORK_")},
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
    env.update(overrides)
    return env


def _run_deploy_ready(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-S", "scripts/check_deploy_ready.py"],
        check=False,
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
    )


def test_deploy_readiness_accepts_strong_configuration() -> None:
    assert validate_deploy_settings(_settings()) == []


def test_get_settings_parses_bounty_board_issue_number(monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_BOUNTY_BOARD_ISSUE_NUMBER", "785")

    assert get_settings().bounty_board_issue_number == 785


def test_deploy_readiness_rejects_invalid_bounty_board_issue_number() -> None:
    errors = validate_deploy_settings(_settings(bounty_board_issue_number=0))

    assert "MERGEWORK_BOUNTY_BOARD_ISSUE_NUMBER must be a positive integer" in errors


def test_get_settings_rejects_malformed_bounty_board_issue_number(monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_BOUNTY_BOARD_ISSUE_NUMBER", "notanumber")

    try:
        get_settings()
    except ValueError as exc:
        assert str(exc) == "MERGEWORK_BOUNTY_BOARD_ISSUE_NUMBER must be an integer"
    else:
        raise AssertionError("expected malformed bounty board issue number to fail")


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


def test_deploy_readiness_rejects_reused_secret_values() -> None:
    reused_secret = "shared-secret-8efc3925bb8746b8a8fd3392c4c48e32"

    errors = validate_deploy_settings(
        _settings(
            github_webhook_secret=reused_secret,
            cookie_secret=reused_secret,
        )
    )

    assert "deploy secrets must use distinct values" in errors


def test_deploy_readiness_rejects_secret_whitespace_and_control_characters() -> None:
    errors = validate_deploy_settings(
        _settings(
            github_webhook_secret=" webhook-8efc3925bb8746b8a8fd3392c4c48e32",
            github_oauth_client_secret="oauth-7818e79f9d3a4a1d82ff0e1b9f0b8e42 ",
            admin_token="admin-14dcaab83bb245f2bfb5d5c21a9bb55b\nextra",
            cookie_secret="cookie-27fd1c41324a4bdcb2e4014adc3a6108\x7f",
        )
    )

    assert (
        "MERGEWORK_GITHUB_WEBHOOK_SECRET must not include leading or trailing whitespace" in errors
    )
    assert (
        "MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET must not include leading or trailing whitespace"
        in errors
    )
    assert "MERGEWORK_ADMIN_TOKEN must not include control characters" in errors
    assert "MERGEWORK_COOKIE_SECRET must not include control characters" in errors


def test_deploy_readiness_rejects_non_persistent_sqlite_database_urls() -> None:
    memory_errors = validate_deploy_settings(_settings(database_url="sqlite:///:memory:"))
    driver_memory_errors = validate_deploy_settings(
        _settings(database_url="sqlite+pysqlite:///:memory:")
    )
    relative_errors = validate_deploy_settings(
        _settings(database_url="sqlite:///mergework.sqlite3")
    )
    driver_relative_errors = validate_deploy_settings(
        _settings(database_url="sqlite+pysqlite:///mergework.sqlite3")
    )
    dot_relative_errors = validate_deploy_settings(
        _settings(database_url="sqlite:///./mergework.sqlite3")
    )
    empty_errors = validate_deploy_settings(_settings(database_url="sqlite:////"))

    assert "MERGEWORK_DATABASE_URL must use a persistent sqlite file" in memory_errors
    assert "MERGEWORK_DATABASE_URL must use a persistent sqlite file" in driver_memory_errors
    assert "MERGEWORK_DATABASE_URL sqlite paths must be absolute for deploys" in relative_errors
    assert (
        "MERGEWORK_DATABASE_URL sqlite paths must be absolute for deploys" in driver_relative_errors
    )
    assert "MERGEWORK_DATABASE_URL sqlite paths must be absolute for deploys" in dot_relative_errors
    assert "MERGEWORK_DATABASE_URL must use a persistent sqlite file" in empty_errors


def test_deploy_readiness_rejects_malformed_postgres_database_urls() -> None:
    missing_host_errors = validate_deploy_settings(
        _settings(database_url="postgresql:///mergework")
    )
    empty_host_errors = validate_deploy_settings(
        _settings(database_url="postgresql://:5432/mergework")
    )
    missing_database_errors = validate_deploy_settings(
        _settings(database_url="postgresql://db.example.test")
    )
    empty_database_errors = validate_deploy_settings(
        _settings(database_url="postgresql://db.example.test/")
    )
    invalid_port_errors = validate_deploy_settings(
        _settings(database_url="postgresql://db.example.test:notaport/mergework")
    )
    out_of_range_port_errors = validate_deploy_settings(
        _settings(database_url="postgresql://db.example.test:70000/mergework")
    )
    empty_port_errors = validate_deploy_settings(
        _settings(database_url="postgresql://db.example.test:/mergework")
    )
    whitespace_host_errors = validate_deploy_settings(
        _settings(database_url="postgresql://bad host/mergework")
    )
    underscore_host_errors = validate_deploy_settings(
        _settings(database_url="postgresql://db_example.test/mergework")
    )
    unmatched_bracket_errors = validate_deploy_settings(_settings(database_url="postgresql://[::1"))

    assert "MERGEWORK_DATABASE_URL must include a database host" in missing_host_errors
    assert "MERGEWORK_DATABASE_URL must include a database host" in empty_host_errors
    assert "MERGEWORK_DATABASE_URL must include a database name" in missing_database_errors
    assert "MERGEWORK_DATABASE_URL must include a database name" in empty_database_errors
    assert "MERGEWORK_DATABASE_URL must include a valid database port" in invalid_port_errors
    assert "MERGEWORK_DATABASE_URL must include a valid database port" in out_of_range_port_errors
    assert "MERGEWORK_DATABASE_URL must include a valid database port" in empty_port_errors
    assert "MERGEWORK_DATABASE_URL must include a valid database host" in whitespace_host_errors
    assert "MERGEWORK_DATABASE_URL must include a valid database host" in underscore_host_errors
    assert "MERGEWORK_DATABASE_URL must include a valid database host" in unmatched_bracket_errors


def test_deploy_readiness_rejects_database_url_missing_whitespace_or_control() -> None:
    missing_errors = validate_deploy_settings(_settings(database_url=""))
    blank_errors = validate_deploy_settings(_settings(database_url="   "))
    whitespace_errors = validate_deploy_settings(
        _settings(database_url=" postgresql://mergework:password@db.example.test/mergework")
    )
    control_errors = validate_deploy_settings(
        _settings(database_url="postgresql://mergework:password@db.example.test/mergework\nextra")
    )

    assert "MERGEWORK_DATABASE_URL is required" in missing_errors
    assert "MERGEWORK_DATABASE_URL is required" in blank_errors
    assert "MERGEWORK_DATABASE_URL must not include leading or trailing whitespace" in (
        whitespace_errors
    )
    assert "MERGEWORK_DATABASE_URL must not include control characters" in control_errors


def test_deploy_readiness_accepts_absolute_sqlite_and_external_database_urls() -> None:
    socket_database_url = "postgresql://mergework:password@/mergework?host=/var/run/postgresql"

    assert (
        validate_deploy_settings(
            _settings(database_url="sqlite:////srv/mergework/data/app.sqlite3")
        )
        == []
    )
    assert (
        validate_deploy_settings(
            _settings(database_url="sqlite+pysqlite:////srv/mergework/data/app.sqlite3")
        )
        == []
    )
    assert validate_deploy_settings(_settings(database_url="sqlite:///C:/data/app.sqlite3")) == []
    assert (
        validate_deploy_settings(
            _settings(database_url="postgresql://mergework:password@db.example.test/mergework")
        )
        == []
    )
    assert (
        validate_deploy_settings(
            _settings(
                database_url="postgresql+psycopg://mergework:password@db.example.test/mergework"
            )
        )
        == []
    )
    assert (
        validate_deploy_settings(
            _settings(
                database_url="postgres+psycopg://mergework:password@db.example.test/mergework"
            )
        )
        == []
    )
    assert validate_deploy_settings(_settings(database_url=socket_database_url)) == []


def test_deploy_readiness_rejects_malformed_database_url_schemes() -> None:
    missing_scheme_errors = validate_deploy_settings(_settings(database_url="not-a-url"))
    unsupported_scheme_errors = validate_deploy_settings(
        _settings(database_url="ftp://db.example.test/mergework")
    )
    sqlite_lookalike_errors = validate_deploy_settings(
        _settings(database_url="sqlitefoo:////srv/mergework/data/app.sqlite3")
    )

    expected = "MERGEWORK_DATABASE_URL must use sqlite, postgresql, or postgres"
    assert expected in missing_scheme_errors
    assert expected in unsupported_scheme_errors
    assert expected in sqlite_lookalike_errors


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


def test_deploy_readiness_rejects_malformed_oauth_client_id() -> None:
    whitespace_errors = validate_deploy_settings(_settings(github_oauth_client_id=" client-id"))
    control_errors = validate_deploy_settings(_settings(github_oauth_client_id="client-id\nextra"))

    assert "MERGEWORK_GITHUB_OAUTH_CLIENT_ID must not include leading or trailing whitespace" in (
        whitespace_errors
    )
    assert "MERGEWORK_GITHUB_OAUTH_CLIENT_ID must not include control characters" in control_errors


def test_deploy_readiness_rejects_public_base_url_text_errors() -> None:
    empty_errors = validate_deploy_settings(_settings(public_base_url=""))
    whitespace_errors = validate_deploy_settings(
        _settings(public_base_url=" https://staging.mrwk.example.test")
    )
    trailing_whitespace_errors = validate_deploy_settings(
        _settings(public_base_url="https://staging.mrwk.example.test ")
    )
    control_errors = validate_deploy_settings(
        _settings(public_base_url="https://staging.mrwk.example.test\nextra")
    )

    assert "MERGEWORK_PUBLIC_BASE_URL is required" in empty_errors
    assert (
        "MERGEWORK_PUBLIC_BASE_URL must not include leading or trailing whitespace"
        in whitespace_errors
    )
    assert (
        "MERGEWORK_PUBLIC_BASE_URL must not include leading or trailing whitespace"
        in trailing_whitespace_errors
    )
    assert "MERGEWORK_PUBLIC_BASE_URL must not include control characters" in control_errors


def test_deploy_readiness_rejects_non_admin_accepted_labelers() -> None:
    errors = validate_deploy_settings(
        _settings(
            admin_logins=("alice",),
            github_accepted_labelers=("alice", "bob"),
        )
    )

    assert "MERGEWORK_GITHUB_ACCEPTED_LABELERS must be included in MERGEWORK_ADMIN_LOGINS" in errors


def test_deploy_readiness_rejects_duplicate_admin_or_labeler_logins() -> None:
    errors = validate_deploy_settings(
        _settings(
            admin_logins=("alice", "alice"),
            github_accepted_labelers=("alice", "alice"),
        )
    )

    assert "MERGEWORK_ADMIN_LOGINS must not include duplicate logins" in errors
    assert "MERGEWORK_GITHUB_ACCEPTED_LABELERS must not include duplicate logins" in errors


def test_deploy_readiness_rejects_invalid_admin_or_labeler_logins() -> None:
    errors = validate_deploy_settings(
        _settings(
            admin_logins=("bad_login",),
            github_accepted_labelers=("bad_login",),
        )
    )

    assert "MERGEWORK_ADMIN_LOGINS must contain valid GitHub logins" in errors
    assert "MERGEWORK_GITHUB_ACCEPTED_LABELERS must contain valid GitHub logins" in errors


def test_deploy_readiness_rejects_empty_login_csv_entries(monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_DATABASE_URL", "sqlite:////srv/mergework/data/app.sqlite3")
    monkeypatch.setenv("MERGEWORK_PUBLIC_BASE_URL", "https://staging.mrwk.example.test")
    monkeypatch.setenv(
        "MERGEWORK_GITHUB_WEBHOOK_SECRET", "webhook-8efc3925bb8746b8a8fd3392c4c48e32"
    )
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv(
        "MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET", "oauth-7818e79f9d3a4a1d82ff0e1b9f0b8e42"
    )
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-14dcaab83bb245f2bfb5d5c21a9bb55b")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "cookie-27fd1c41324a4bdcb2e4014adc3a6108")
    monkeypatch.setenv("MERGEWORK_ADMIN_LOGINS", "alice,")
    monkeypatch.setenv("MERGEWORK_GITHUB_ACCEPTED_LABELERS", "alice,,")

    errors = validate_deploy_settings(get_settings())

    assert "MERGEWORK_ADMIN_LOGINS must not include empty entries" in errors
    assert "MERGEWORK_GITHUB_ACCEPTED_LABELERS must not include empty entries" in errors
    assert "MERGEWORK_ADMIN_LOGINS must not include duplicate logins" not in errors
    assert "MERGEWORK_GITHUB_ACCEPTED_LABELERS must not include duplicate logins" not in errors
    assert "MERGEWORK_GITHUB_ACCEPTED_LABELERS must be included in MERGEWORK_ADMIN_LOGINS" not in (
        errors
    )


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


def test_deploy_readiness_rejects_public_base_url_userinfo() -> None:
    errors = validate_deploy_settings(
        _settings(public_base_url="https://operator:secret@mrwk.example.test")
    )

    assert "MERGEWORK_PUBLIC_BASE_URL must not include userinfo" in errors


def test_deploy_readiness_rejects_malformed_public_base_url_hosts() -> None:
    empty_host_errors = validate_deploy_settings(_settings(public_base_url="https://:443"))
    invalid_ipv4_errors = validate_deploy_settings(
        _settings(public_base_url="https://999.999.999.999")
    )
    invalid_port_errors = validate_deploy_settings(
        _settings(public_base_url="https://api.example:notaport")
    )
    out_of_range_port_errors = validate_deploy_settings(
        _settings(public_base_url="https://api.example:70000")
    )
    unbracketed_ipv6_errors = validate_deploy_settings(
        _settings(public_base_url="https://2001:db8::1")
    )
    unmatched_bracket_errors = validate_deploy_settings(_settings(public_base_url="https://[::1"))
    empty_bracket_errors = validate_deploy_settings(_settings(public_base_url="https://[]"))
    bracketed_dns_errors = validate_deploy_settings(
        _settings(public_base_url="https://[api.example]")
    )
    dot_host_errors = validate_deploy_settings(_settings(public_base_url="https://."))
    empty_label_errors = validate_deploy_settings(_settings(public_base_url="https://api..example"))
    leading_hyphen_errors = validate_deploy_settings(
        _settings(public_base_url="https://-api.example")
    )
    trailing_hyphen_errors = validate_deploy_settings(
        _settings(public_base_url="https://api-.example")
    )
    underscore_errors = validate_deploy_settings(
        _settings(public_base_url="https://api_host.example")
    )

    expected = "MERGEWORK_PUBLIC_BASE_URL must include a valid host"
    assert expected in empty_host_errors
    assert expected in invalid_ipv4_errors
    assert expected in invalid_port_errors
    assert expected in out_of_range_port_errors
    assert expected in unbracketed_ipv6_errors
    assert expected in unmatched_bracket_errors
    assert expected in empty_bracket_errors
    assert expected in bracketed_dns_errors
    assert expected in dot_host_errors
    assert expected in empty_label_errors
    assert expected in leading_hyphen_errors
    assert expected in trailing_hyphen_errors
    assert expected in underscore_errors


def test_deploy_readiness_rejects_dotless_public_base_url_host() -> None:
    errors = validate_deploy_settings(_settings(public_base_url="https://internal"))

    assert "MERGEWORK_PUBLIC_BASE_URL must use a public host" in errors


def test_deploy_readiness_script_runs_directly_from_source() -> None:
    result = _run_deploy_ready(_deploy_ready_env())

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Deploy readiness check passed."


def test_deploy_readiness_subprocess_env_ignores_parent_mergework_values(monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT", "0")

    result = _run_deploy_ready(_deploy_ready_env())

    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == "Deploy readiness check passed."


def test_deploy_readiness_accepts_documented_treasury_executor_envs() -> None:
    result = _run_deploy_ready(
        _deploy_ready_env(
            MERGEWORK_TREASURY_EXECUTOR_ENABLED="1",
            MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS="300",
            MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT="25",
            MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS="60",
        )
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Deploy readiness check passed."


def test_deploy_readiness_accepts_treasury_executor_minimum_bounds() -> None:
    result = _run_deploy_ready(
        _deploy_ready_env(
            MERGEWORK_TREASURY_EXECUTOR_ENABLED="1",
            MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS="15",
            MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT="1",
            MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS="30",
        )
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Deploy readiness check passed."


@pytest.mark.parametrize(
    ("name", "value", "expected_error"),
    [
        (
            "MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS",
            "14",
            "MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS must be at least 15",
        ),
        (
            "MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT",
            "0",
            "MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT must be at least 1",
        ),
        (
            "MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS",
            "29",
            "MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS must be at least 30",
        ),
    ],
)
def test_deploy_readiness_rejects_enabled_treasury_executor_invalid_envs(
    name: str, value: str, expected_error: str
) -> None:
    result = _run_deploy_ready(
        _deploy_ready_env(
            MERGEWORK_TREASURY_EXECUTOR_ENABLED="1",
            **{name: value},
        )
    )

    assert result.returncode == 1, result.stdout
    assert "Deploy readiness check failed:" in result.stdout
    assert f"- {expected_error}" in result.stdout


def test_deploy_readiness_rejects_loopback_public_base_url() -> None:
    localhost_errors = validate_deploy_settings(_settings(public_base_url="https://localhost:8000"))
    ipv4_errors = validate_deploy_settings(_settings(public_base_url="https://127.0.0.1"))
    ipv6_errors = validate_deploy_settings(_settings(public_base_url="https://[::1]"))

    assert "MERGEWORK_PUBLIC_BASE_URL must not use a loopback host" in localhost_errors
    assert "MERGEWORK_PUBLIC_BASE_URL must not use a loopback host" in ipv4_errors
    assert "MERGEWORK_PUBLIC_BASE_URL must not use a loopback host" in ipv6_errors


def test_deploy_readiness_rejects_private_public_base_url_ip_literals() -> None:
    private_ipv4_errors = validate_deploy_settings(_settings(public_base_url="https://10.0.0.5"))
    rfc1918_errors = validate_deploy_settings(_settings(public_base_url="https://192.168.1.20"))
    rfc1918_midblock_errors = validate_deploy_settings(
        _settings(public_base_url="https://172.16.2.3")
    )
    link_local_errors = validate_deploy_settings(_settings(public_base_url="https://169.254.10.20"))
    private_ipv6_errors = validate_deploy_settings(_settings(public_base_url="https://[fd00::1]"))

    expected = "MERGEWORK_PUBLIC_BASE_URL must not use a private or link-local host"
    assert expected in private_ipv4_errors
    assert expected in rfc1918_errors
    assert expected in rfc1918_midblock_errors
    assert expected in link_local_errors
    assert expected in private_ipv6_errors


def test_deploy_readiness_rejects_non_global_public_base_url_ip_literals() -> None:
    cgnat_errors = validate_deploy_settings(_settings(public_base_url="https://100.64.0.1"))
    multicast_errors = validate_deploy_settings(_settings(public_base_url="https://224.0.0.1"))
    multicast_ipv6_errors = validate_deploy_settings(_settings(public_base_url="https://[ff02::1]"))

    expected = "MERGEWORK_PUBLIC_BASE_URL must use a globally routable host"
    assert expected in cgnat_errors
    assert expected in multicast_errors
    assert expected in multicast_ipv6_errors


def test_deploy_readiness_allows_global_public_base_url_ip_literal() -> None:
    assert validate_deploy_settings(_settings(public_base_url="https://8.8.8.8")) == []


def test_deploy_readiness_allows_numeric_dns_labels_longer_than_ipv4_octets() -> None:
    errors = validate_deploy_settings(_settings(public_base_url="https://1234.5.6.7.example"))

    assert errors == []
