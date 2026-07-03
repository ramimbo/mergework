"""Tests for shared gh CLI maintenance constants."""

from __future__ import annotations

from scripts.gh_cli_constants import GH_TIMEOUT_SECONDS


def test_gh_timeout_is_positive_int() -> None:
    assert isinstance(GH_TIMEOUT_SECONDS, int)
    assert GH_TIMEOUT_SECONDS > 0
