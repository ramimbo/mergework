from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import check_live_bounty_closing_refs
from scripts.check_live_bounty_closing_refs import analyze_closing_refs, main

ROOT = Path(__file__).resolve().parents[1]


def test_closing_ref_check_flags_open_public_bounty_reference(tmp_path, capsys) -> None:
    fixture = {
        "bounties": [
            {"id": 117, "issue_number": 936, "status": "open"},
            {"id": 118, "issue_number": 944, "status": "open"},
            {"id": 120, "issue_number": 950, "status": "paid"},
        ],
        "pull_requests": [
            {
                "number": 991,
                "title": "Refactor activity helpers",
                "body": "Closes #936",
                "url": "https://github.com/ramimbo/mergework/pull/991",
            },
            {
                "number": 1015,
                "title": "Publish OpenAPI constraints",
                "body": "Bounty #944\nRefs #944",
                "url": "https://github.com/ramimbo/mergework/pull/1015",
            },
            {
                "number": 1200,
                "title": "Final closeout",
                "body": "Resolves #950",
                "url": "https://github.com/ramimbo/mergework/pull/1200",
            },
        ],
    }

    report = analyze_closing_refs(fixture)

    assert report["summary"] == {
        "pull_requests": 3,
        "open_public_bounties": 2,
        "closing_references_to_open_bounties": 1,
    }
    assert report["violations"][0]["pull_request"] == 991
    assert report["violations"][0]["issue_number"] == 936
    assert report["violations"][0]["matched_reference"] == "Closes #936"

    input_path = tmp_path / "closing_refs.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8")
    exit_code = main(["--input", str(input_path), "--format", "text", "--fail-on-issues"])
    assert exit_code == 1
    assert "PR #991" in capsys.readouterr().out


def test_closing_ref_check_accepts_non_closing_bounty_refs() -> None:
    report = analyze_closing_refs(
        {
            "bounties": [{"id": 118, "issue_number": 944, "status": "open"}],
            "pull_requests": [
                {
                    "number": 1015,
                    "title": "Bounty #944: publish OpenAPI constraints",
                    "body": "Bounty #944\nRefs #944\n/claim #944",
                }
            ],
        }
    )

    assert report["summary"]["closing_references_to_open_bounties"] == 0
    assert report["violations"] == []


def test_closing_ref_check_script_entrypoint_loads_shared_parser() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_live_bounty_closing_refs.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout


def test_closing_ref_check_loads_specific_prs(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["gh", "pr", "view"]:
            stdout = json.dumps(
                {
                    "number": int(args[3]),
                    "title": "Guard bounty closeout",
                    "body": "Closes #944",
                    "url": "https://github.com/ramimbo/mergework/pull/1015",
                    "state": "OPEN",
                }
            )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(check_live_bounty_closing_refs.subprocess, "run", fake_run)

    prs = check_live_bounty_closing_refs._load_pull_requests("ramimbo/mergework", "open", [1015])

    assert prs[0]["number"] == 1015
    assert calls[0][:4] == ["gh", "pr", "view", "1015"]
