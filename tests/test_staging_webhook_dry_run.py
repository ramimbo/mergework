from __future__ import annotations

import httpx
import pytest

from scripts.staging_webhook_dry_run import (
    _dry_run_contributor,
    _dry_run_repo,
    _enforce_staging_target,
    _parse_object_response,
    _validate_http_url,
    main,
)


def _set_required_dry_run_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MERGEWORK_STAGING_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("MERGEWORK_ADMIN_TOKEN", "admin-token")
    monkeypatch.setenv("MERGEWORK_GITHUB_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setenv("MERGEWORK_GITHUB_ACCEPTED_LABELERS", "maintainer")


def test_staging_webhook_dry_run_allows_loopback_hosts() -> None:
    _enforce_staging_target("http://localhost:8000")
    _enforce_staging_target("http://127.0.0.1:8000")
    _enforce_staging_target("http://[::1]:8000")


def test_staging_webhook_dry_run_rejects_non_staging_public_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MERGEWORK_ALLOW_NON_STAGING_DRY_RUN", raising=False)

    with pytest.raises(RuntimeError, match="MERGEWORK_STAGING_BASE_URL"):
        _enforce_staging_target("https://mrwk.example.test")


def test_staging_webhook_dry_run_rejects_url_credentials() -> None:
    with pytest.raises(RuntimeError, match="must not include username or password"):
        _validate_http_url("https://operator:secret@staging.mrwk.example.test")
    with pytest.raises(RuntimeError, match="must not include username or password"):
        _validate_http_url("https://operator@staging.mrwk.example.test")
    with pytest.raises(RuntimeError, match="must not include username or password"):
        _validate_http_url("https://:secret@staging.mrwk.example.test")
    with pytest.raises(RuntimeError, match="must not include username or password"):
        _validate_http_url("ftp://operator:secret@staging.mrwk.example.test")

    _validate_http_url("https://staging.mrwk.example.test")


def test_staging_webhook_dry_run_rejects_non_object_json_response() -> None:
    response = httpx.Response(
        200,
        json=["not", "an", "object"],
        request=httpx.Request("POST", "https://staging.mrwk.example.test/api"),
    )

    with pytest.raises(RuntimeError, match="returned a non-object JSON payload"):
        _parse_object_response("https://staging.mrwk.example.test/api", response)


def test_staging_webhook_dry_run_uses_valid_default_identity_envs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MERGEWORK_DRY_RUN_REPO", raising=False)
    monkeypatch.delenv("MERGEWORK_DRY_RUN_CONTRIBUTOR", raising=False)

    assert _dry_run_repo() == "ramimbo/mergework"
    assert _dry_run_contributor() == "mergework-dry-run"


@pytest.mark.parametrize(
    "repo",
    [
        "",
        "   ",
        "ramimbo",
        "ramimbo/",
        "/mergework",
        "ramimbo/mergework/extra",
        "https://github.com/ramimbo/mergework",
    ],
)
def test_staging_webhook_dry_run_rejects_malformed_repo_before_http(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repo: str,
) -> None:
    _set_required_dry_run_env(monkeypatch)
    monkeypatch.setenv("MERGEWORK_DRY_RUN_REPO", repo)
    monkeypatch.setattr(
        "scripts.staging_webhook_dry_run.httpx.post",
        lambda *_args, **_kwargs: pytest.fail("dry run posted before repo validation"),
    )

    assert main() == 1
    assert "MERGEWORK_DRY_RUN_REPO" in capsys.readouterr().err


@pytest.mark.parametrize("contributor", ["", "   "])
def test_staging_webhook_dry_run_rejects_blank_contributor_before_http(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    contributor: str,
) -> None:
    _set_required_dry_run_env(monkeypatch)
    monkeypatch.setenv("MERGEWORK_DRY_RUN_REPO", "ramimbo/mergework")
    monkeypatch.setenv("MERGEWORK_DRY_RUN_CONTRIBUTOR", contributor)
    monkeypatch.setattr(
        "scripts.staging_webhook_dry_run.httpx.post",
        lambda *_args, **_kwargs: pytest.fail("dry run posted before contributor validation"),
    )

    assert main() == 1
    assert "MERGEWORK_DRY_RUN_CONTRIBUTOR" in capsys.readouterr().err
