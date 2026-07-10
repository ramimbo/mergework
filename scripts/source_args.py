from __future__ import annotations

import argparse


def _require_non_empty(parser: argparse.ArgumentParser, value: str | None, *, label: str) -> str:
    if value is None or not value.strip():
        parser.error(f"{label} must not be empty or whitespace-only")
    return value.strip()


def validate_source_args(
    parser: argparse.ArgumentParser,
    *,
    input_value: str | None,
    repo_value: str | None,
    fix: bool = False,
) -> tuple[str | None, str]:
    if input_value is not None and repo_value is not None:
        parser.error("argument --repo: not allowed with argument --input")
    if input_value is None and repo_value is None:
        parser.error("one of the arguments --input --repo is required")
    if input_value is not None:
        input_arg = _require_non_empty(parser, input_value, label="--input")
        if fix:
            parser.error("--fix requires --repo, not --input")
        return input_arg, ""
    return None, _require_non_empty(parser, repo_value, label="--repo")
