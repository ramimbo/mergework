from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import proposed_work_queue
from scripts.proposed_work_queue import analyze_queue, format_markdown_report, main

ROOT = Path(__file__).resolve().parents[1]

COMPLETE_PROPOSED_WORK_BODY = """
## Problem
CLI-created proposed-work issues may not get labels.

## Evidence
GitHub rejects non-maintainer label edits.

## Proposed work
Detect template-shaped issues without relying only on labels.

## Expected value
Maintainers can triage the full queue.

## Possible acceptance criteria
Unlabeled complete issues appear in the report.

## Evidence or tests required
Fixture tests cover labeled and unlabeled issues.

## Duplicate search
No matching focused implementation exists.

## Out of scope
No label mutation or payment behavior.
"""


def test_proposed_work_queue_includes_unlabeled_template_issues() -> None:
    report = analyze_queue(
        {
            "issues": [
                {
                    "number": 1,
                    "title": "Idea: missing sections but labeled",
                    "url": "https://github.com/ramimbo/mergework/issues/1",
                    "body": "## Problem\nOnly one section.",
                    "labels": [{"name": "proposed-work"}],
                    "author": {"login": "maintainer"},
                },
                {
                    "number": 2,
                    "title": "Proposed work: handle unlabeled CLI intake",
                    "url": "https://github.com/ramimbo/mergework/issues/2",
                    "body": COMPLETE_PROPOSED_WORK_BODY,
                    "labels": [],
                    "author": {"login": "cli-user"},
                },
                {
                    "number": 3,
                    "title": "Proposed work: vague title only",
                    "body": "Please do the thing.",
                    "labels": [],
                    "author": {"login": "idea-user"},
                },
                {
                    "number": 4,
                    "title": "Bug: unrelated issue",
                    "body": COMPLETE_PROPOSED_WORK_BODY,
                    "labels": [],
                    "author": {"login": "bug-user"},
                },
            ]
        }
    )

    assert report["summary"] == {
        "issues_seen": 4,
        "proposed_work": 2,
        "labeled": 1,
        "title_body_fallback": 1,
        "missing_label": 1,
        "missing_required_sections": 1,
        "ignored_proposed_titles": 1,
    }
    rows = {row["issue_number"]: row for row in report["rows"]}
    assert rows[1]["detection"] == "label"
    assert "missing_required_sections" in rows[1]["warnings"]
    assert rows[2]["detection"] == "title_body_fallback"
    assert rows[2]["warnings"] == ["missing_proposed_work_label"]
    assert rows[2]["missing_sections"] == []
    assert 3 not in rows
    assert 4 not in rows


def test_proposed_work_queue_markdown_report_is_pasteable() -> None:
    report = analyze_queue(
        {
            "issues": [
                {
                    "number": 2,
                    "title": "Proposed work: handle unlabeled CLI intake",
                    "url": "https://github.com/ramimbo/mergework/issues/2",
                    "body": COMPLETE_PROPOSED_WORK_BODY,
                    "labels": [],
                    "author": {"login": "cli-user"},
                }
            ]
        }
    )

    markdown = format_markdown_report(report)

    assert markdown.startswith("## Proposed-Work Queue")
    assert "- **title body fallback**: 1" in markdown
    assert "[#2](https://github.com/ramimbo/mergework/issues/2)" in markdown
    assert "`title_body_fallback`" in markdown
    assert "missing_proposed_work_label" in markdown


def test_proposed_work_queue_script_entrypoint_loads_shared_parser() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/proposed_work_queue.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout


def test_proposed_work_queue_live_loader_uses_read_only_issue_list(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        stdout = json.dumps(
            [
                {
                    "number": 2,
                    "title": "Proposed work: handle unlabeled CLI intake",
                    "url": "https://github.com/ramimbo/mergework/issues/2",
                    "body": COMPLETE_PROPOSED_WORK_BODY,
                    "labels": [],
                    "author": {"login": "cli-user"},
                }
            ]
        )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(proposed_work_queue.subprocess, "run", fake_run)

    data = proposed_work_queue.load_live_queue("ramimbo/mergework")
    report = analyze_queue(data)

    assert report["summary"]["title_body_fallback"] == 1
    assert calls == [
        [
            "gh",
            "issue",
            "list",
            "--repo",
            "ramimbo/mergework",
            "--state",
            "open",
            "--limit",
            "201",
            "--json",
            "number,title,url,body,labels,author",
        ]
    ]


def test_proposed_work_queue_rejects_non_read_only_gh_command() -> None:
    with pytest.raises(RuntimeError, match="refusing non-read-only gh issue command"):
        proposed_work_queue._run_gh_json(["gh", "issue", "edit", "2"])
    with pytest.raises(RuntimeError, match="refusing non-read-only gh issue command"):
        proposed_work_queue._run_gh_json(["gh", "issue", "create", "--title", "bad"])
    with pytest.raises(RuntimeError, match="refusing non-read-only gh api command"):
        proposed_work_queue._run_gh_json(
            ["gh", "api", "repos/ramimbo/mergework/issues", "-f", "x=y"]
        )
    with pytest.raises(RuntimeError, match="refusing non-read-only gh api command"):
        proposed_work_queue._run_gh_json(
            ["gh", "api", "repos/ramimbo/mergework/issues", "--method", "POST"]
        )


def test_proposed_work_queue_wraps_missing_gh_binary(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(proposed_work_queue.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="gh executable not found"):
        proposed_work_queue.load_live_queue("ramimbo/mergework")


def test_proposed_work_queue_allows_explicit_read_only_gh_api_methods(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"ok": True}),
            stderr="",
        )

    monkeypatch.setattr(proposed_work_queue.subprocess, "run", fake_run)

    assert proposed_work_queue._run_gh_json(
        ["gh", "api", "repos/ramimbo/mergework/issues", "--method", "GET"]
    ) == {"ok": True}
    assert proposed_work_queue._run_gh_json(
        ["gh", "api", "repos/ramimbo/mergework/issues", "--method", "HEAD"]
    ) == {"ok": True}
    assert calls == [
        ["gh", "api", "repos/ramimbo/mergework/issues", "--method", "GET"],
        ["gh", "api", "repos/ramimbo/mergework/issues", "--method", "HEAD"],
    ]


def test_proposed_work_queue_rejects_when_safety_cap_reached(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        issues = [
            {"number": number, "title": "x", "body": "", "labels": []}
            for number in range(proposed_work_queue.GH_ISSUE_SAFETY_CAP)
        ]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(issues),
            stderr="",
        )

    monkeypatch.setattr(proposed_work_queue.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="issue list reached the 201 item safety cap"):
        proposed_work_queue.load_live_queue("ramimbo/mergework")


def test_proposed_work_queue_main_reads_fixture(tmp_path, capsys) -> None:
    fixture_path = tmp_path / "proposed-work.json"
    fixture_path.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "number": 2,
                        "title": "Proposed work: handle unlabeled CLI intake",
                        "body": COMPLETE_PROPOSED_WORK_BODY,
                        "labels": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["--input", str(fixture_path), "--format", "json"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["title_body_fallback"] == 1
