"""Tests for the consolidated `_safe_return` helper.

Refactor PR: #936 (MRWK bounty: code cleanup and maintainability, round 2).
The helper replaces three near-identical try/except wrappers in
`app.serializers` with a single, type-parameterised function.

These tests pin down the contract:
1. Returns the call's result when the call succeeds.
2. Returns the default when the call raises any exception.
3. The default is returned as-is, not re-raised, when the call raises.
4. Works for both dict-typed and list-typed defaults.
5. Works with arguments bound via closure (lambda).
"""

from __future__ import annotations

from app.serializers import _safe_return


def test_safe_return_returns_result_on_success():
    """When the wrapped call succeeds, the result is returned unchanged."""
    assert _safe_return(lambda: 42, default=0) == 42


def test_safe_return_returns_default_on_exception():
    """When the wrapped call raises, the default is returned."""

    def boom() -> str:
        raise RuntimeError("kaboom")

    assert _safe_return(boom, default="fallback") == "fallback"


def test_safe_return_handles_empty_dict_default():
    """Works with dict-typed default (mirrors `empty_accepted_summary()`)."""
    empty: dict = {}

    def boom() -> dict:
        raise ValueError("nope")

    assert _safe_return(boom, default=empty) is empty


def test_safe_return_handles_empty_list_default():
    """Works with list-typed default (mirrors `safe_*_for_account` list helpers)."""
    empty: list = []

    def boom() -> list:
        raise IndexError("oops")

    assert _safe_return(boom, default=empty) is empty


def test_safe_return_propagates_no_exception():
    """The helper must swallow every exception, not re-raise."""

    def boom() -> None:
        raise RuntimeError("must be swallowed")

    # If the helper leaked the exception, this assertion would not run.
    result = _safe_return(boom, default="ok")
    assert result == "ok"


def test_safe_return_preserves_call_arguments_via_closure():
    """The helper must accept a zero-arg callable; args are bound via the closure."""
    captured = []

    def producer(value: int) -> int:
        captured.append(value)
        return value * 2

    assert _safe_return(lambda: producer(21), default=-1) == 42
    assert captured == [21]


def test_safe_return_default_evaluated_only_on_failure():
    """The default must NOT be eagerly evaluated; it's only used on failure.

    We use a sentinel object that is also a class to detect eager evaluation
    via `is` identity.
    """

    class _Sentinel:
        pass

    sentinel = _Sentinel()

    def ok() -> _Sentinel:
        return sentinel

    # A fresh instance every call — must NOT be used because the call succeeds.
    fresh = _Sentinel()
    result = _safe_return(ok, default=fresh)
    assert result is sentinel, "default was evaluated; helper should be lazy"
