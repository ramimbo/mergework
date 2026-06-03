from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_BATCH_LIMIT = 25
DEFAULT_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 15
MIN_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS = 30
MAX_BATCH_LIMIT = 200
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"", "0", "false", "no", "off"}


@dataclass(frozen=True)
class ExecutorConfig:
    enabled: bool
    interval_seconds: int
    batch_limit: int
    bounty_board_refresh_interval_seconds: int


def _enabled_from_env(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError("MERGEWORK_TREASURY_EXECUTOR_ENABLED must be true or false")


def _positive_int_from_env(
    env: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int | None = None
) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return value


def executor_config_from_env(env: Mapping[str, str] | None = None) -> ExecutorConfig:
    source = os.environ if env is None else env
    return ExecutorConfig(
        enabled=_enabled_from_env(source.get("MERGEWORK_TREASURY_EXECUTOR_ENABLED")),
        interval_seconds=_positive_int_from_env(
            source,
            "MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS",
            DEFAULT_INTERVAL_SECONDS,
            minimum=MIN_INTERVAL_SECONDS,
        ),
        batch_limit=_positive_int_from_env(
            source,
            "MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT",
            DEFAULT_BATCH_LIMIT,
            minimum=1,
            maximum=MAX_BATCH_LIMIT,
        ),
        bounty_board_refresh_interval_seconds=_positive_int_from_env(
            source,
            "MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS",
            DEFAULT_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS,
            minimum=MIN_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS,
        ),
    )
