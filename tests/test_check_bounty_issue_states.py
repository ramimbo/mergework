from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import check_bounty_issue_states
from scripts.check_bounty_issue_states import analyze_issue_states, main

ROOT = Path(__file__).resolve().parents[1]


def test_issue_state_check_flags_closed_github_issue_for_open_public_bounty(
    tmp_path, capsys
) -> None:
    fixture = {
        "bounties": [
            {
                "id": 117,
                "issue_number": 936,
                "status": "open",
                "availability_state": "open",
                "awards_paid": 2,
                "max_awards": 8,
                "effective_awards_remaining": 6,
            },
            {
                "id": 118,
                "issue_number": 944,
                "status": "open",
                "availability_state": "open",
                "effective_awards_remaining": 4,
            },
        ],
        "issues": [
            {"number": 936, "state": "CLOSED", "url": "https://github.com/x/y/issues/936"},
            {"number": 944, "state": "OPEN", "url": "https://github.com/x/y/issues/944"},
        ],
    }

    report = analyze_issue_states(fixture)

    assert report["summary"] == {
        "open_public_bounties": 2,
        "closed_or_missing_github_issues": 1,
    }
    assert report["violations"][0] == {
        "issue_number": 936,
        "bounty_id": 117,
        "availability_state": "open",
        "effective_awards_remaining": 6,
        "awards_paid": 2,
        "max_awards": 8,
        "issue_state": "closed",
        "issue_url": "https://github.com/x/y/issues/936",
        "detail": "Open public bounty #936 has GitHub issue state CLOSED",
    }

    input_path = tmp_path / "issue_states.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8")
    exit_code = main(["--input", str(input_path), "--format", "text", "--fail-on-issues"])
    assert exit_code == 1
    assert "#936" in capsys.readouterr().out


def test_issue_state_check_accepts_open_github_issue() -> None:
    report = analyze_issue_states(
        {
            "bounties": [{"id": 118, "issue_number": 944, "status": "open"}],
            "issues": [{"number": 944, "state": "OPEN"}],
        }
    )

    assert report["summary"]["closed_or_missing_github_issues"] == 0
    assert report["violations"] == []


def test_issue_state_check_script_entrypoint_loads_shared_parser() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_bounty_issue_states.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout


def test_issue_state_check_fix_reopens_closed_issues(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["gh", "issue", "reopen"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(check_bounty_issue_states.subprocess, "run", fake_run)

    check_bounty_issue_states.reopen_violations(
        "ramimbo/mergework",
        [
            {"issue_number": 936, "issue_state": "closed"},
            {"issue_number": 944, "issue_state": "missing"},
        ],
    )

    assert calls == [["gh", "issue", "reopen", "936", "--repo", "ramimbo/mergework"]]
