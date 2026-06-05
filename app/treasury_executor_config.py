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


@dataclass(frozen=True)
class _PositiveIntEnv:
    name: str
    default: int
    minimum: int
    maximum: int | None = None


_INTERVAL_SECONDS_ENV = _PositiveIntEnv(
    "MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS",
    DEFAULT_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
)
_BATCH_LIMIT_ENV = _PositiveIntEnv(
    "MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT",
    DEFAULT_BATCH_LIMIT,
    1,
    MAX_BATCH_LIMIT,
)
_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS_ENV = _PositiveIntEnv(
    "MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS",
    DEFAULT_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS,
    MIN_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS,
)


def _enabled_from_env(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError("MERGEWORK_TREASURY_EXECUTOR_ENABLED must be true or false")


def _positive_int_from_env(env: Mapping[str, str], setting: _PositiveIntEnv) -> int:
    raw = env.get(setting.name, str(setting.default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{setting.name} must be an integer") from exc
    if value < setting.minimum:
        raise ValueError(f"{setting.name} must be at least {setting.minimum}")
    if setting.maximum is not None and value > setting.maximum:
        raise ValueError(f"{setting.name} must be at most {setting.maximum}")
    return value


def executor_config_from_env(env: Mapping[str, str] | None = None) -> ExecutorConfig:
    source = os.environ if env is None else env
    return ExecutorConfig(
        enabled=_enabled_from_env(source.get("MERGEWORK_TREASURY_EXECUTOR_ENABLED")),
        interval_seconds=_positive_int_from_env(
            source,
            _INTERVAL_SECONDS_ENV,
        ),
        batch_limit=_positive_int_from_env(
            source,
            _BATCH_LIMIT_ENV,
        ),
        bounty_board_refresh_interval_seconds=_positive_int_from_env(
            source,
            _BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS_ENV,
        ),
    )
