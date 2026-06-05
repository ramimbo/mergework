from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Callable, Sequence

from app.config import get_settings
from app.github_bounty_board import refresh_bounty_board_issue
from app.treasury_executor import execute_due_treasury_proposals
from app.treasury_executor_config import ExecutorConfig, executor_config_from_env


def run_once(config: ExecutorConfig) -> dict[str, object]:
    settings = get_settings()
    return execute_due_treasury_proposals(
        settings.database_url,
        github_issue_token=settings.github_issue_token,
        public_base_url=settings.public_base_url,
        executed_by="treasury-executor",
        limit=config.batch_limit,
        bounty_board_issue_number=settings.bounty_board_issue_number,
    )


def run_bounty_board_refresh_once() -> dict[str, object]:
    settings = get_settings()
    return refresh_bounty_board_issue(
        settings.database_url,
        github_token=settings.github_issue_token,
        public_base_url=settings.public_base_url,
        issue_number=settings.bounty_board_issue_number,
    )


def _sleep_until(next_run_at: float) -> None:
    delay = max(1.0, next_run_at - time.monotonic())
    time.sleep(delay)


def _run_logged_pass(
    runner: Callable[[], dict[str, object]], *, success_message: str, failure_message: str
) -> bool:
    try:
        report = runner()
    except Exception:
        logging.exception(failure_message)
        return False
    logging.info("%s %s", success_message, json.dumps(report, sort_keys=True))
    return True


def run_enabled_loop(config: ExecutorConfig, *, once: bool) -> int:
    next_executor_at = 0.0
    next_board_refresh_at = 0.0

    while True:
        now = time.monotonic()
        if now >= next_executor_at:
            success = _run_logged_pass(
                lambda: run_once(config),
                success_message="treasury executor report",
                failure_message="treasury executor pass failed",
            )
            if once and not success:
                return 1
            if once:
                return 0
            now = time.monotonic()
            next_executor_at = now + config.interval_seconds
            next_board_refresh_at = now + config.bounty_board_refresh_interval_seconds
            _sleep_until(min(next_executor_at, next_board_refresh_at))
            continue

        if now >= next_board_refresh_at:
            _run_logged_pass(
                run_bounty_board_refresh_once,
                success_message="bounty board refresh report",
                failure_message="bounty board refresh failed",
            )
            now = time.monotonic()
            next_board_refresh_at = now + config.bounty_board_refresh_interval_seconds

        _sleep_until(min(next_executor_at, next_board_refresh_at))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute due MergeWork treasury proposals.")
    parser.add_argument("--once", action="store_true", help="Run one enabled pass and exit.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = executor_config_from_env()
    if not config.enabled:
        logging.info("treasury executor disabled by MERGEWORK_TREASURY_EXECUTOR_ENABLED")
        if args.once:
            return 0
        while True:
            time.sleep(config.interval_seconds)
    return run_enabled_loop(config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
