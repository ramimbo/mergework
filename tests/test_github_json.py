from __future__ import annotations

import json
import subprocess
import urllib.error

import pytest

from scripts import github_json


def test_run_gh_json_decodes_success(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        assert kwargs["capture_output"] is True
        assert kwargs["encoding"] == "utf-8"
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps({"ok": True}))

    monkeypatch.setattr(github_json.subprocess, "run", fake_run)

    assert github_json.run_gh_json(["gh", "issue", "view", "1"]) == {"ok": True}


def test_run_gh_reports_stderr_on_failure(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        raise subprocess.CalledProcessError(2, args, output="out", stderr="err")

    monkeypatch.setattr(github_json.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="gh command failed") as exc_info:
        github_json.run_gh(["gh", "pr", "list"])

    assert "stderr:\nerr" in str(exc_info.value)


def test_fetch_json_wraps_url_errors(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(github_json.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="failed to fetch JSON"):
        github_json.fetch_json("https://api.example.test/items")