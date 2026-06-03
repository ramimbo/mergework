from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Sequence

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


def run_enabled_loop(config: ExecutorConfig, *, once: bool) -> int:
    next_executor_at = 0.0
    next_board_refresh_at = 0.0

    while True:
        now = time.monotonic()
        if now >= next_executor_at:
            try:
                report = run_once(config)
                logging.info("treasury executor report %s", json.dumps(report, sort_keys=True))
            except Exception:
                logging.exception("treasury executor pass failed")
                if once:
                    return 1
            if once:
                return 0
            now = time.monotonic()
            next_executor_at = now + config.interval_seconds
            next_board_refresh_at = now + config.bounty_board_refresh_interval_seconds
            _sleep_until(min(next_executor_at, next_board_refresh_at))
            continue

        if now >= next_board_refresh_at:
            try:
                report = run_bounty_board_refresh_once()
                logging.info("bounty board refresh report %s", json.dumps(report, sort_keys=True))
            except Exception:
                logging.exception("bounty board refresh failed")
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
