from __future__ import annotations

import pytest

import scripts.treasury_executor as executor_script
from scripts.treasury_executor import ExecutorConfig, executor_config_from_env


def test_executor_config_defaults_to_disabled() -> None:
    config = executor_config_from_env({})

    assert config.enabled is False
    assert config.interval_seconds == 300
    assert config.batch_limit == 25
    assert config.bounty_board_refresh_interval_seconds == 60


def test_executor_config_reads_explicit_production_settings() -> None:
    config = executor_config_from_env(
        {
            "MERGEWORK_TREASURY_EXECUTOR_ENABLED": "true",
            "MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS": "60",
            "MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT": "5",
            "MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS": "90",
        }
    )

    assert config.enabled is True
    assert config.interval_seconds == 60
    assert config.batch_limit == 5
    assert config.bounty_board_refresh_interval_seconds == 90


def test_executor_config_accepts_documented_enable_flag() -> None:
    config = executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_ENABLED": "1"})

    assert config.enabled is True


def test_executor_config_accepts_bounds() -> None:
    config = executor_config_from_env(
        {
            "MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS": "15",
            "MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT": "200",
            "MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS": "30",
        }
    )

    assert config.interval_seconds == 15
    assert config.batch_limit == 200
    assert config.bounty_board_refresh_interval_seconds == 30


def test_executor_config_rejects_invalid_numbers() -> None:
    with pytest.raises(ValueError, match="MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS"):
        executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS": "0"})

    with pytest.raises(ValueError, match="MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT"):
        executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT": "0"})

    with pytest.raises(ValueError, match="MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT"):
        executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT": "201"})

    with pytest.raises(ValueError, match="MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS"):
        executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS": "notanumber"})

    with pytest.raises(ValueError, match="MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS"):
        executor_config_from_env({"MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS": "29"})

    with pytest.raises(ValueError, match="MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS"):
        executor_config_from_env({"MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS": "notanumber"})


def test_board_refresh_once_uses_lightweight_board_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class Settings:
        database_url = "sqlite:////srv/mergework/data/mergework.sqlite3"
        github_issue_token = "github-issue-token"
        public_base_url = "https://mrwk.example"
        bounty_board_issue_number = 785

    def fake_refresh(
        db_url: str,
        *,
        github_token: str,
        public_base_url: str,
        issue_number: int | None,
    ) -> dict[str, object]:
        calls.append(
            {
                "db_url": db_url,
                "github_token": github_token,
                "public_base_url": public_base_url,
                "issue_number": issue_number,
            }
        )
        return {"status": "updated", "issue_number": issue_number}

    monkeypatch.setattr(executor_script, "get_settings", lambda: Settings())
    monkeypatch.setattr(executor_script, "refresh_bounty_board_issue", fake_refresh)

    assert executor_script.run_bounty_board_refresh_once() == {
        "status": "updated",
        "issue_number": 785,
    }
    assert calls == [
        {
            "db_url": "sqlite:////srv/mergework/data/mergework.sqlite3",
            "github_token": "github-issue-token",
            "public_base_url": "https://mrwk.example",
            "issue_number": 785,
        }
    ]


def test_enabled_loop_refreshes_board_between_executor_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ExecutorConfig(
        enabled=True,
        interval_seconds=300,
        batch_limit=1,
        bounty_board_refresh_interval_seconds=60,
    )
    now = 0.0
    events: list[tuple[str, float]] = []

    def fake_monotonic() -> float:
        return now

    def fake_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    def fake_run_once(config: ExecutorConfig) -> dict[str, object]:
        events.append(("executor", now))
        return {"status": "ok"}

    def fake_board_refresh_once() -> dict[str, object]:
        events.append(("board", now))
        raise KeyboardInterrupt

    monkeypatch.setattr(executor_script.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(executor_script.time, "sleep", fake_sleep)
    monkeypatch.setattr(executor_script, "run_once", fake_run_once)
    monkeypatch.setattr(
        executor_script,
        "run_bounty_board_refresh_once",
        fake_board_refresh_once,
        raising=False,
    )

    with pytest.raises(KeyboardInterrupt):
        executor_script.run_enabled_loop(config, once=False)

    assert events == [("executor", 0.0), ("board", 60.0)]


def test_executor_once_returns_failure_when_pass_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_once(config: ExecutorConfig) -> dict[str, object]:
        raise RuntimeError("executor failed")

    monkeypatch.setattr(
        executor_script,
        "executor_config_from_env",
        lambda: ExecutorConfig(
            enabled=True,
            interval_seconds=15,
            batch_limit=1,
            bounty_board_refresh_interval_seconds=60,
        ),
    )
    monkeypatch.setattr(executor_script, "run_once", fake_run_once)

    assert executor_script.main(["--once"]) == 1


def test_executor_main_reports_invalid_config_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        executor_script,
        "executor_config_from_env",
        lambda: (_ for _ in ()).throw(
            ValueError("MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS must be at least 15")
        ),
    )

    assert executor_script.main(["--once"]) == 1
    assert "treasury executor configuration invalid" in caplog.text
    assert "MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS must be at least 15" in caplog.text
