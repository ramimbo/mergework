from __future__ import annotations

import pytest

from scripts.staging_webhook_dry_run import _enforce_staging_target, _validate_http_url


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
