from __future__ import annotations

import json
import subprocess
from typing import Any

DEFAULT_GH_TIMEOUT_SECONDS = 30

_NON_READ_ONLY_GH_API = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_NON_READ_ONLY_GH_SUBCOMMANDS = frozenset(
    {"comment", "edit", "close", "reopen", "merge", "review", "delete"}
)


def _command_text(args: list[str]) -> str:
    return " ".join(args)


def assert_read_only_gh_command(args: list[str]) -> None:
    """Reject gh invocations that could mutate GitHub state."""
    if args[:2] == ["gh", "api"]:
        for flag in ("--method", "-X"):
            if flag not in args:
                continue
            index = args.index(flag)
            if index + 1 >= len(args):
                continue
            if args[index + 1].upper() in _NON_READ_ONLY_GH_API:
                raise RuntimeError(f"refusing non-read-only gh api command: {_command_text(args)}")
    if any(arg in {"issue", "pr"} for arg in args) and any(
        arg in _NON_READ_ONLY_GH_SUBCOMMANDS for arg in args
    ):
        raise RuntimeError(f"refusing non-read-only gh command: {_command_text(args)}")


def run_gh(args: list[str], *, timeout_seconds: int = DEFAULT_GH_TIMEOUT_SECONDS) -> str:
    """Run a read-only gh command and return stdout text."""
    assert_read_only_gh_command(args)
    command = _command_text(args)
    try:
        completed = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"gh command timed out after {timeout_seconds}s: {command}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            "GitHub CLI executable 'gh' was not found; install gh and ensure it is on PATH "
            "before using live --repo mode"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "gh command failed "
            f"(exit {exc.returncode}): {command}\n"
            f"stdout:\n{exc.stdout or exc.output or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        ) from exc
    return completed.stdout


def run_gh_json(args: list[str], *, timeout_seconds: int = DEFAULT_GH_TIMEOUT_SECONDS) -> Any:
    """Run a read-only gh command and parse JSON stdout."""
    return json.loads(run_gh(args, timeout_seconds=timeout_seconds))


def ensure_json_list(data: Any, *, label: str) -> list[Any]:
    if not isinstance(data, list):
        raise RuntimeError(f"{label} returned non-list JSON ({type(data).__name__})")
    return data


def ensure_json_object(data: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise RuntimeError(f"{label} returned non-object JSON ({type(data).__name__})")
    return data


def run_gh_json_list(
    args: list[str], *, label: str | None = None, timeout_seconds: int = DEFAULT_GH_TIMEOUT_SECONDS
) -> list[Any]:
    context = label or _command_text(args)
    return ensure_json_list(run_gh_json(args, timeout_seconds=timeout_seconds), label=context)


def run_gh_json_object(
    args: list[str], *, label: str | None = None, timeout_seconds: int = DEFAULT_GH_TIMEOUT_SECONDS
) -> dict[str, Any]:
    context = label or _command_text(args)
    return ensure_json_object(run_gh_json(args, timeout_seconds=timeout_seconds), label=context)
