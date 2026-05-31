from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import pr_queue_health
from scripts.pr_queue_health import analyze_queue, format_markdown_report, format_text_report, main

ROOT = Path(__file__).resolve().parents[1]


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
        "live_bounties": 2,
        "non_live_bounties": 0,
        "closed_or_exhausted_bounties": 1,
        "closed_bounty_references": 1,
        "non_live_bounty_references": 0,
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


def test_pr_queue_health_script_entrypoint_loads_shared_parser() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/pr_queue_health.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout


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


def test_pr_queue_health_accepts_github_linking_keywords() -> None:
    references = (
        "Bounty #310",
        "Claim #310",
        "Claims #310",
        "Ref #310",
        "Refs #310",
        "Reference #310",
        "References #310",
        "Fix #310",
        "Fixes #310",
        "Fixed #310",
        "Close #310",
        "Closes #310",
        "Closed #310",
        "Resolve #310",
        "Resolves #310",
        "Resolved #310",
        "Refs `#310`",
        "/claim `#310`",
    )
    report = analyze_queue(
        {
            "bounties": [{"number": 310, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [
                {
                    "number": index,
                    "title": f"Harden bounty queue checks {index}",
                    "body": reference,
                    "merge_state": "clean",
                    "labels": [],
                }
                for index, reference in enumerate(references, start=1)
            ],
        }
    )

    assert report["summary"]["missing_bounty_references"] == 0
    assert report["missing_bounty_references"] == []


def test_pr_queue_health_flags_open_issue_without_live_bounty_signals() -> None:
    report = analyze_queue(
        {
            "bounties": [
                {
                    "number": 310,
                    "state": "OPEN",
                    "awards_remaining": 1,
                    "labels": [{"name": "mrwk:bounty"}],
                    "comments": [
                        {"body": "Reserved on MergeWork: https://mrwk.online/bounties/310"}
                    ],
                },
                {
                    "number": 311,
                    "state": "OPEN",
                    "awards_remaining": 1,
                    "labels": [],
                    "comments": [],
                },
                {
                    "number": 312,
                    "state": "OPEN",
                    "awards_remaining": 1,
                    "labels": [{"name": "mrwk:bounty"}],
                    "comments": [],
                },
            ],
            "pull_requests": [
                {
                    "number": 1,
                    "title": "Live bounty work",
                    "body": "Bounty #310",
                    "merge_state": "clean",
                    "labels": [],
                },
                {
                    "number": 2,
                    "title": "Proposed-only bounty work",
                    "body": "Bounty #311",
                    "merge_state": "clean",
                    "labels": [],
                },
                {
                    "number": 3,
                    "title": "Label-only bounty work",
                    "body": "Bounty #312",
                    "merge_state": "clean",
                    "labels": [],
                },
            ],
        }
    )

    assert report["summary"]["live_bounties"] == 1
    assert report["summary"]["non_live_bounties"] == 2
    assert report["summary"]["non_live_bounty_references"] == 2
    details = {
        item["pull_request"]: item["detail"] for item in report["non_live_bounty_references"]
    }
    assert details[2] == "Referenced bounty #311 is not live claimable: missing mrwk:bounty label"
    assert (
        details[3] == "Referenced bounty #312 is not live claimable: "
        "missing Reserved on MergeWork claims-open comment"
    )


def test_pr_queue_health_keeps_legacy_fixture_open_bounty_compatibility() -> None:
    report = analyze_queue(
        {
            "bounties": [{"number": 310, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [
                {
                    "number": 8,
                    "title": "Legacy fixture bounty work",
                    "body": "Bounty #310",
                    "merge_state": "clean",
                    "labels": [],
                }
            ],
        }
    )

    assert report["summary"]["live_bounties"] == 1
    assert report["summary"]["non_live_bounty_references"] == 0
    assert report["non_live_bounty_references"] == []


@pytest.mark.parametrize("reference", ("Fixes #310abc", "Fixes #310_abc", "Fixes #310-abc"))
def test_pr_queue_health_rejects_linking_keyword_issue_suffix(reference: str) -> None:
    report = analyze_queue(
        {
            "bounties": [{"number": 310, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [
                {
                    "number": 8,
                    "title": "Harden bounty queue checks",
                    "body": reference,
                    "merge_state": "clean",
                    "labels": [],
                }
            ],
        }
    )

    assert report["summary"]["missing_bounty_references"] == 1
    assert report["missing_bounty_references"][0]["pull_request"] == 8


def test_pr_queue_health_ignores_oversized_numeric_bounty_refs() -> None:
    oversized_ref = "9" * 5000
    report = analyze_queue(
        {
            "bounties": [{"number": 406, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [
                {
                    "number": 41,
                    "title": "Bound bounty references",
                    "body": f"Refs #{oversized_ref}\nRefs #406",
                    "merge_state": "clean",
                    "labels": [],
                }
            ],
        }
    )

    assert report["summary"]["missing_bounty_references"] == 0
    assert report["summary"]["closed_bounty_references"] == 0


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
    assert "- **live bounties**: 1" in markdown
    assert "- **non live bounty references**: 0" in markdown
    assert "### Missing bounty references" in markdown
    assert (
        "- [PR #2](https://github.com/ramimbo/mergework/pull/2): "
        "Improve bounty filters (No bounty reference such as Bounty #<issue>, "
        "Refs #<issue>, Fixes #<issue>, or /claim #<issue> found)"
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


def test_pr_queue_health_live_loader_includes_referenced_issue_comments(monkeypatch) -> None:
    viewed_numbers = []

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            stdout = json.dumps(
                [
                    {
                        "number": 8,
                        "title": "Queue guard",
                        "url": "https://github.com/ramimbo/mergework/pull/8",
                        "body": "Bounty #310",
                        "labels": [],
                        "mergeStateStatus": "clean",
                    }
                ]
            )
        elif args[:3] == ["gh", "issue", "list"]:
            stdout = json.dumps(
                [
                    {
                        "number": 310,
                        "title": "MRWK bounty: queue guard",
                        "state": "OPEN",
                        "labels": [{"name": "mrwk:bounty"}],
                    },
                    {
                        "number": 311,
                        "title": "MRWK bounty: unrelated docs",
                        "state": "OPEN",
                        "labels": [{"name": "mrwk:bounty"}],
                    },
                ]
            )
        elif args[:3] == ["gh", "issue", "view"]:
            viewed_numbers.append(args[3])
            stdout = json.dumps(
                {
                    "number": 310,
                    "title": "MRWK bounty: queue guard",
                    "state": "OPEN",
                    "labels": [{"name": "mrwk:bounty"}],
                    "comments": [
                        {"body": "Reserved on MergeWork: https://mrwk.online/bounties/310"}
                    ],
                }
            )
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(pr_queue_health.subprocess, "run", fake_run)

    data = pr_queue_health.load_live_queue("ramimbo/mergework")
    report = analyze_queue(data)

    assert data["bounties"][0]["comments"]
    assert viewed_numbers == ["310"]
    assert report["summary"]["live_bounties"] == 2
    assert report["summary"]["non_live_bounties"] == 0
    assert report["summary"]["non_live_bounty_references"] == 0


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
