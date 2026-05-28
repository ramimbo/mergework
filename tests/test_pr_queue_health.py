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


def test_pr_queue_health_accepts_claim_command_reference() -> None:
    report = analyze_queue(
        {
            "bounties": [{"number": 310, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [
                {
                    "number": 8,
                    "title": "Harden bounty submission checks",
                    "body": "/claim #310",
                    "merge_state": "clean",
                    "labels": [],
                }
            ],
        }
    )

    assert report["summary"]["missing_bounty_references"] == 0
    assert report["missing_bounty_references"] == []


def test_pr_queue_health_ignores_boolean_explicit_bounty_refs() -> None:
    report = analyze_queue(
        {
            "bounties": [{"number": 406, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [
                {
                    "number": 531,
                    "title": "Boolean refs are not issue refs",
                    "body": "",
                    "bounty_refs": [True, False],
                    "merge_state": "clean",
                    "labels": [],
                },
                {
                    "number": 532,
                    "title": "Valid explicit issue ref still counts",
                    "body": "",
                    "bounty_refs": [True, 406],
                    "merge_state": "clean",
                    "labels": [],
                },
            ],
        }
    )

    assert report["summary"]["missing_bounty_references"] == 1
    assert report["missing_bounty_references"][0]["pull_request"] == 531
    assert report["closed_bounty_references"] == []


def test_pr_queue_health_ignores_native_bounty_ids_when_issue_ref_is_present() -> None:
    report = analyze_queue(
        {
            "bounties": [{"number": 406, "state": "OPEN", "awards_remaining": 16}],
            "pull_requests": [
                {
                    "number": 524,
                    "title": "Refs #406: Reject malformed IPv4 public URLs",
                    "body": (
                        "Focused #406 small fix. Evidence: live bounty #66 / issue #406 "
                        "preflight returned status=open."
                    ),
                    "merge_state": "clean",
                    "labels": [],
                },
                {
                    "number": 525,
                    "title": "Guard another #406 edge case",
                    "body": "MRWK bounty #66 maps to issue #406.",
                    "merge_state": "clean",
                    "labels": [],
                },
                {
                    "number": 528,
                    "title": "Ignore spaced live bounty evidence ids",
                    "body": (
                        "Evidence: live  bounty #66 and live\tbounty #67 both map to issue #406."
                    ),
                    "merge_state": "clean",
                    "labels": [],
                },
                {
                    "number": 526,
                    "title": "Ignore native #406 evidence ids",
                    "body": (
                        "Evidence: native bounty #66 / issue #406 preflight returned status=open."
                    ),
                    "merge_state": "clean",
                    "labels": [],
                },
                {
                    "number": 527,
                    "title": "Ignore internal #406 evidence ids",
                    "body": "Evidence: internal bounty #66, issue #406 is the GitHub bounty.",
                    "merge_state": "clean",
                    "labels": [],
                },
            ],
        }
    )

    assert report["summary"]["closed_bounty_references"] == 0
    assert report["summary"]["missing_bounty_references"] == 0
    assert report["closed_bounty_references"] == []
    assert report["missing_bounty_references"] == []


def test_pr_queue_health_requires_issue_ref_when_only_native_bounty_id_is_present() -> None:
    report = analyze_queue(
        {
            "bounties": [{"number": 406, "state": "OPEN", "awards_remaining": 16}],
            "pull_requests": [
                {
                    "number": 529,
                    "title": "Needs GitHub issue reference",
                    "body": "Evidence: live bounty #66 returned status=open.",
                    "merge_state": "clean",
                    "labels": [],
                },
                {
                    "number": 530,
                    "title": "Longer words still count as bounty refs",
                    "body": "Evidence: relive bounty #406 returned status=open.",
                    "merge_state": "clean",
                    "labels": [],
                },
            ],
        }
    )

    assert report["summary"]["missing_bounty_references"] == 1
    assert report["missing_bounty_references"][0]["pull_request"] == 529


def test_pr_queue_health_markdown_report_includes_required_sections() -> None:
    report = analyze_queue(
        {
            "bounties": [
                {"number": 292, "state": "OPEN", "awards_remaining": 13},
                {"number": 293, "state": "CLOSED", "awards_remaining": 0},
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
    )

    markdown = format_markdown_report(report)

    assert markdown.startswith("## PR Queue Health Summary")
    assert "- **pull requests**: 4" in markdown
    assert "### Closed or exhausted bounty references" in markdown
    assert (
        "- [PR #1](https://github.com/ramimbo/mergework/pull/1): "
        "Add public bounty summary API (Referenced bounty #293 is not payable)"
    ) in markdown
    assert "### Missing bounty references" in markdown
    assert (
        "- [PR #2](https://github.com/ramimbo/mergework/pull/2): "
        "Improve bounty filters "
        "(No Bounty #<issue>, Issue #<issue>, Refs #<issue>, or /claim #<issue> found)"
    ) in markdown
    assert "### Dirty or unstable merge state" in markdown
    assert "Merge state is dirty" in markdown
    assert "### Needs info" in markdown
    assert "PR has mrwk:needs-info label" in markdown
    assert "### Likely duplicate bounty scope" in markdown
    assert "- Bounty #292: guard mcp bounty search oversized numeric query (#3, #4)" in markdown


def test_pr_queue_health_markdown_no_issues_output_is_pasteable(tmp_path, capsys) -> None:
    fixture = {
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
    input_path = tmp_path / "queue.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8")

    exit_code = main(["--input", str(input_path), "--format", "markdown"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert output.startswith("## PR Queue Health Summary")
    assert "- **pull requests**: 1" in output
    assert "No queue-health issues found." in output


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
