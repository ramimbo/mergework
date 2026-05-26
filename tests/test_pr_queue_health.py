from __future__ import annotations

import json
import subprocess

import pytest

from scripts import pr_queue_health
from scripts.pr_queue_health import analyze_queue, format_markdown_report, format_text_report, main


def test_pr_queue_health_flags_required_queue_cases(tmp_path, capsys) -> None:
    fixture = {
        "bounties": [
            {"number": 292, "state": "OPEN", "awards_remaining": 13},
            {"number": 293, "state": "CLOSED", "awards_remaining": 0},
            {"number": 310, "state": "OPEN", "awards_remaining": 8},
        ],
        "pull_requests": [
            {
                "number": 1,
                "title": "Add public bounty summary API",
                "url": "https://github.com/ramimbo/mergework/pull/1",
                "body": "Refs #293",
                "merge_state": "clean",
                "labels": [],
            },
            {
                "number": 2,
                "title": "Improve bounty filters",
                "url": "https://github.com/ramimbo/mergework/pull/2",
                "body": "",
                "merge_state": "clean",
                "labels": [],
            },
            {
                "number": 3,
                "title": "Guard MCP bounty search oversized numeric query",
                "url": "https://github.com/ramimbo/mergework/pull/3",
                "body": "Bounty #292",
                "merge_state": "dirty",
                "labels": ["mrwk:needs-info"],
            },
            {
                "number": 4,
                "title": "Guard MCP bounty search oversized numeric query",
                "url": "https://github.com/ramimbo/mergework/pull/4",
                "body": "Refs #292",
                "merge_state": "unknown",
                "labels": [],
            },
        ],
    }

    report = analyze_queue(fixture)

    assert report["summary"] == {
        "pull_requests": 4,
        "open_bounties": 2,
        "closed_or_exhausted_bounties": 1,
        "closed_bounty_references": 1,
        "missing_bounty_references": 1,
        "dirty_or_unstable_merge_state": 2,
        "needs_info": 1,
        "duplicate_scope_groups": 1,
    }
    assert report["closed_bounty_references"][0]["pull_request"] == 1
    assert report["missing_bounty_references"][0]["pull_request"] == 2
    assert {item["pull_request"] for item in report["dirty_or_unstable_merge_state"]} == {3, 4}
    assert report["needs_info"][0]["pull_request"] == 3
    assert report["duplicate_scope_groups"] == [
        {
            "bounty": 292,
            "scope": "guard mcp bounty search oversized numeric query",
            "pull_requests": [3, 4],
        }
    ]

    input_path = tmp_path / "queue.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8")
    exit_code = main(["--input", str(input_path), "--format", "json", "--fail-on-issues"])
    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["pull_requests"] == 4


def test_pr_queue_health_text_report_is_pasteable() -> None:
    report = analyze_queue(
        {
            "bounties": [{"number": 310, "state": "OPEN", "awards_remaining": 5}],
            "pull_requests": [
                {
                    "number": 8,
                    "title": "Review open PRs",
                    "body": "Refs #310",
                    "merge_state": "clean",
                    "labels": [],
                }
            ],
        }
    )

    text = format_text_report(report)

    assert "PR queue health summary" in text
    assert "pull requests: 1" in text
    assert "No queue-health issues found." in text


def test_pr_queue_health_markdown_report_has_tables() -> None:
    report = analyze_queue(
        {
            "bounties": [
                {"number": 292, "state": "OPEN", "awards_remaining": 1},
                {"number": 293, "state": "CLOSED", "awards_remaining": 0},
            ],
            "pull_requests": [
                {
                    "number": 12,
                    "title": "Update stale docs",
                    "url": "https://github.com/ramimbo/mergework/pull/12",
                    "body": "Refs #293",
                    "merge_state": "clean",
                    "labels": [],
                },
                {
                    "number": 13,
                    "title": "Add queue report",
                    "body": "",
                    "merge_state": "dirty",
                    "labels": ["mrwk:needs-info"],
                },
            ],
        }
    )

    markdown = format_markdown_report(report)

    assert markdown.startswith("# PR Queue Health")
    assert "| Metric | Count |" in markdown
    assert "| closed bounty references | 1 |" in markdown
    assert "### Closed or exhausted bounty references" in markdown
    assert "| #12 | [Update stale docs](https://github.com/ramimbo/mergework/pull/12)" in markdown
    assert "### Missing bounty references" in markdown
    assert "### Dirty or unstable merge state" in markdown
    assert "### Needs info" in markdown


def test_pr_queue_health_markdown_cli_output(tmp_path, capsys) -> None:
    input_path = tmp_path / "queue.json"
    input_path.write_text(
        json.dumps(
            {
                "bounties": [{"number": 310, "state": "OPEN", "awards_remaining": 5}],
                "pull_requests": [
                    {
                        "number": 8,
                        "title": "Review open PRs",
                        "body": "Refs #310",
                        "merge_state": "clean",
                        "labels": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["--input", str(input_path), "--format", "markdown"])

    assert exit_code == 0
    assert "No queue-health issues found." in capsys.readouterr().out


def test_pr_queue_health_wraps_gh_failures(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=2,
            cmd=["gh", "pr", "list"],
            output="partial",
            stderr="network unavailable",
        )

    monkeypatch.setattr(pr_queue_health.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="gh command failed"):
        pr_queue_health._run_gh_json(["gh", "pr", "list"])


def test_pr_queue_health_wraps_gh_timeouts(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["gh", "pr", "list"], timeout=30)

    monkeypatch.setattr(pr_queue_health.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="gh command timed out"):
        pr_queue_health._run_gh_json(["gh", "pr", "list"])


def test_pr_queue_health_fails_fast_when_issue_fetch_hits_cap(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            stdout = "[]"
        elif args[:3] == ["gh", "issue", "list"]:
            stdout = json.dumps(
                [
                    {"number": number, "title": "MRWK bounty: many", "state": "OPEN"}
                    for number in range(1, 202)
                ]
            )
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(pr_queue_health.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="issue list reached the 201 item safety cap"):
        pr_queue_health.load_live_queue("ramimbo/mergework")


def test_pr_queue_health_fails_fast_when_pr_fetch_hits_cap(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            stdout = json.dumps(
                [
                    {
                        "number": number,
                        "title": "Open PR",
                        "body": "Refs #1",
                        "labels": [],
                        "mergeStateStatus": "clean",
                    }
                    for number in range(1, 202)
                ]
            )
        elif args[:3] == ["gh", "issue", "list"]:
            stdout = "[]"
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(pr_queue_health.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="pr list reached the 201 item safety cap"):
        pr_queue_health.load_live_queue("ramimbo/mergework")
