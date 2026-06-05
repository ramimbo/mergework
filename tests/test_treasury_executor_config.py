from __future__ import annotations

import pytest

from app.treasury_executor_config import (
    executor_config_from_env,
)


def test_executor_config_uses_shared_positive_int_specs() -> None:
    config = executor_config_from_env({})

    assert config.interval_seconds == 300
    assert config.batch_limit == 25
    assert config.bounty_board_refresh_interval_seconds == 60


def test_positive_int_spec_preserves_validation_messages() -> None:
    with pytest.raises(
        ValueError, match="MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS must be at least 15"
    ):
        executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_INTERVAL_SECONDS": "14"})

    with pytest.raises(
        ValueError, match="MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT must be at most 200"
    ):
        executor_config_from_env({"MERGEWORK_TREASURY_EXECUTOR_BATCH_LIMIT": "201"})

    with pytest.raises(
        ValueError, match="MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS must be an integer"
    ):
        executor_config_from_env({"MERGEWORK_BOUNTY_BOARD_REFRESH_INTERVAL_SECONDS": "notanumber"})
