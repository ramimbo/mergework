from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from scripts.gh_cli import assert_read_only_gh_command, run_gh, run_gh_json


def test_assert_read_only_gh_command_rejects_mutating_api_method() -> None:
    with pytest.raises(RuntimeError, match="refusing non-read-only gh api command"):
        assert_read_only_gh_command(["gh", "api", "repos/x/y/issues/1", "--method", "POST"])


def test_assert_read_only_gh_command_rejects_issue_comment() -> None:
    with pytest.raises(RuntimeError, match="refusing non-read-only gh command"):
        assert_read_only_gh_command(
            ["gh", "issue", "comment", "1", "--repo", "x/y", "--body", "hi"]
        )


def test_run_gh_returns_stdout() -> None:
    completed = type("Completed", (), {"stdout": '{"ok": true}'})()

    with patch("scripts.gh_cli.subprocess.run", return_value=completed):
        assert run_gh(["gh", "api", "repos/x/y"]) == '{"ok": true}'


def test_run_gh_json_parses_payload() -> None:
    payload = {"items": [1, 2]}
    completed = type("Completed", (), {"stdout": json.dumps(payload)})()

    with patch("scripts.gh_cli.subprocess.run", return_value=completed):
        assert run_gh_json(["gh", "api", "repos/x/y"]) == payload


def test_run_gh_wraps_missing_executable() -> None:
    with (
        patch("scripts.gh_cli.subprocess.run", side_effect=FileNotFoundError("gh")),
        pytest.raises(RuntimeError, match="GitHub CLI executable 'gh' was not found"),
    ):
        run_gh(["gh", "api", "repos/x/y"])
