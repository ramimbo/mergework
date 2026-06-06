from __future__ import annotations

import json

import pytest

from scripts import reconcile_payouts


def test_reconcile_payouts_reports_malformed_settings_without_session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_settings() -> object:
        raise ValueError("MERGEWORK_DATABASE_URL is invalid")

    def fail_session_scope(_database_url: str) -> object:
        raise AssertionError("session_scope should not be called for invalid settings")

    monkeypatch.setattr(reconcile_payouts, "get_settings", fail_settings)
    monkeypatch.setattr(reconcile_payouts, "session_scope", fail_session_scope)

    assert reconcile_payouts.main() == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "duplicate_source_urls": [],
        "issues": [
            {
                "message": "MERGEWORK_DATABASE_URL is invalid",
                "status": "configuration_invalid",
            }
        ],
        "summary": {"status": "configuration_invalid"},
    }
