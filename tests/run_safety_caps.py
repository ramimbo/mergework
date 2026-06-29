"""Lightweight test runner for the claim_inventory safety cap changes.

This avoids a hard dependency on pytest by:
  1. Providing a minimal `pytest.raises` shim if pytest is not installed.
  2. Loading each test function from `tests.test_claim_inventory` and
     calling it inside `unittest.TestCase` so we still get pass/fail output.
"""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ModuleNotFoundError:
    pytest = types.ModuleType("pytest")  # type: ignore

    class _Raises:
        def __init__(self, exc: type[BaseException]) -> None:
            self.exc = exc

        def __enter__(self) -> _Raises:
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            if exc_type is None:
                raise AssertionError(f"expected {self.exc.__name__} to be raised")
            if not issubclass(exc_type, self.exc):
                return False
            self._exc = exc
            return True

    def raises(exc: type[BaseException]) -> _Raises:  # type: ignore
        return _Raises(exc)

    pytest.raises = raises  # type: ignore
    sys.modules["pytest"] = pytest

tests_mod = importlib.import_module("tests.test_claim_inventory")

NEW_TESTS = [
    "test_claim_inventory_exposes_safety_caps",
    "test_claim_inventory_public_api_fails_fast_on_bounty_safety_cap",
    "test_claim_inventory_public_api_fails_fast_on_activity_safety_cap",
    "test_claim_inventory_live_mode_fails_fast_on_issue_safety_cap",
    "test_claim_inventory_live_mode_fails_fast_on_pr_safety_cap",
]


def make_case(name: str):
    def _test(self: unittest.TestCase) -> None:
        getattr(tests_mod, name)()

    _test.__name__ = name
    return _test


for name in NEW_TESTS:
    setattr(unittest.TestCase, name, make_case(name))


if __name__ == "__main__":
    suite = unittest.TestSuite()
    for name in NEW_TESTS:
        suite.addTest(unittest.TestLoader().loadTestsFromName(name, unittest.TestCase))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
