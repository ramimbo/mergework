from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.review_bounty_candidates import (
    analyze_candidates,
    format_markdown_report,
    format_text_report,
    load_live_candidates,
    main,
)

ROOT = Path(__file__).resolve().parents[1]


def _quality_check(conclusion: str = "SUCCESS") -> list[dict[str, str]]:
    return [{"name": "Quality, readiness, docs, and image checks", "conclusion": conclusion}]


def _review(login: str, state: str, commit: str) -> dict[str, object]:
    return {
        "author": {"login": login, "is_bot": False},
        "state": state,
        "commit": {"oid": commit},
    }


def test_review_bounty_candidates_classifies_review_states(tmp_path, capsys) -> None:
    fixture = {
        "pull_requests": [
            {
                "number": 1,
                "title": "Self authored change",
                "author": {"login": "reviewer"},
                "headRefOid": "h1",
                "mergeStateStatus": "CLEAN",
                "labels": [],
                "statusCheckRollup": _quality_check(),
                "reviews": [],
            },
            {
                "number": 2,
                "title": "Already reviewed",
                "author": {"login": "alice"},
                "headRefOid": "h2",
                "mergeStateStatus": "CLEAN",
                "labels": [],
                "statusCheckRollup": _quality_check(),
                "reviews": [_review("reviewer", "APPROVED", "h2")],
            },
            {
                "number": 3,
                "title": "Reviewer needs fresh head",
                "author": {"login": "alice"},
                "headRefOid": "h3",
                "mergeStateStatus": "CLEAN",
                "labels": [],
                "statusCheckRollup": _quality_check(),
                "reviews": [_review("reviewer", "APPROVED", "old")],
            },
            {
                "number": 4,
                "title": "Dirty branch",
                "author": {"login": "alice"},
                "headRefOid": "h4",
                "mergeStateStatus": "DIRTY",
                "labels": [],
                "statusCheckRollup": _quality_check(),
                "reviews": [],
            },
            {
                "number": 5,
                "title": "No standard CI",
                "author": {"login": "alice"},
                "headRefOid": "h5",
                "mergeStateStatus": "CLEAN",
                "labels": [],
                "statusCheckRollup": [],
                "reviews": [],
            },
            {
                "number": 6,
                "title": "Needs info",
                "author": {"login": "alice"},
                "headRefOid": "h6",
                "mergeStateStatus": "CLEAN",
                "labels": [{"name": "mrwk:needs-info"}],
                "statusCheckRollup": _quality_check(),
                "reviews": [],
            },
            {
                "number": 7,
                "title": "Waiting for author",
                "author": {"login": "alice"},
                "headRefOid": "h7",
                "mergeStateStatus": "CLEAN",
                "labels": [],
                "statusCheckRollup": _quality_check(),
                "reviews": [_review("bob", "CHANGES_REQUESTED", "h7")],
            },
            {
                "number": 8,
                "title": "Enough review",
                "author": {"login": "alice"},
                "headRefOid": "h8",
                "mergeStateStatus": "CLEAN",
                "labels": [],
                "statusCheckRollup": _quality_check(),
                "reviews": [_review("bob", "APPROVED", "h8")],
            },
            {
                "number": 9,
                "title": "Fresh candidate",
                "author": {"login": "alice"},
                "headRefOid": "h9",
                "mergeStateStatus": "CLEAN",
                "labels": [],
                "statusCheckRollup": _quality_check(),
                "reviews": [],
            },
        ]
    }

    report = analyze_candidates(fixture, reviewer="Reviewer")
    states = {row["pull_request"]: row["state"] for row in report["pull_requests"]}

    assert states == {
        1: "self_authored",
        2: "already_reviewed_current_head_by_reviewer",
        3: "candidate_for_fresh_review",
        4: "dirty_or_conflicted",
        5: "missing_standard_quality_check",
        6: "needs_info",
        7: "waiting_for_author_update",
        8: "already_has_sufficient_current_head_human_reviews",
        9: "candidate_for_fresh_review",
    }
    assert report["summary"]["candidate_for_fresh_review"] == 2
    assert report["pull_requests"][2]["reason"] == "reviewer last reviewed an older head"

    input_path = tmp_path / "candidates.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8")
    exit_code = main(
        [
            "--input",
            str(input_path),
            "--reviewer",
            "reviewer",
            "--format",
            "json",
        ]
    )
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["pull_requests"] == 9


def test_review_bounty_candidates_ignores_author_and_bot_reviews() -> None:
    report = analyze_candidates(
        {
            "pull_requests": [
                {
                    "number": 1,
                    "title": "Bot reviewed only",
                    "author": {"login": "alice"},
                    "headRefOid": "h1",
                    "mergeStateStatus": "CLEAN",
                    "labels": [],
                    "statusCheckRollup": _quality_check(),
                    "reviews": [
                        _review("alice", "APPROVED", "h1"),
                        {
                            "author": {"login": "coderabbitai", "is_bot": True},
                            "state": "APPROVED",
                            "commit": {"oid": "h1"},
                        },
                    ],
                }
            ]
        },
        reviewer="reviewer",
    )

    row = report["pull_requests"][0]
    assert row["state"] == "candidate_for_fresh_review"
    assert row["current_head_human_reviews"] == 0


def test_review_bounty_candidates_marks_bounty_claim_comment_evidence() -> None:
    current_head = "a" * 40
    stale_head = "b" * 40
    report = analyze_candidates(
        {
            "pull_requests": [
                {
                    "number": 11,
                    "title": "Already claimed current head",
                    "url": "https://github.com/ramimbo/mergework/pull/11",
                    "author": {"login": "alice"},
                    "headRefOid": current_head,
                    "mergeStateStatus": "CLEAN",
                    "labels": [],
                    "statusCheckRollup": _quality_check(),
                    "reviews": [],
                },
                {
                    "number": 12,
                    "title": "Dirty claimed old head",
                    "url": "https://github.com/ramimbo/mergework/pull/12",
                    "author": {"login": "alice"},
                    "headRefOid": current_head,
                    "mergeStateStatus": "DIRTY",
                    "labels": [],
                    "statusCheckRollup": _quality_check(),
                    "reviews": [],
                },
                {
                    "number": 13,
                    "title": "Claimed by concise PR comment",
                    "url": "https://github.com/ramimbo/mergework/pull/13",
                    "author": {"login": "alice"},
                    "headRefOid": current_head,
                    "mergeStateStatus": "CLEAN",
                    "labels": [],
                    "statusCheckRollup": _quality_check(),
                    "reviews": [],
                },
                {
                    "number": 14,
                    "title": "Fresh candidate",
                    "url": "https://github.com/ramimbo/mergework/pull/14",
                    "author": {"login": "alice"},
                    "headRefOid": current_head,
                    "mergeStateStatus": "CLEAN",
                    "labels": [],
                    "statusCheckRollup": _quality_check(),
                    "reviews": [],
                },
            ],
            "bounty_claim_comments": [
                {
                    "url": "https://github.com/ramimbo/mergework/issues/654#issuecomment-1",
                    "author": {"login": "reviewer-one"},
                    "createdAt": "2026-06-02T10:00:00Z",
                    "body": (
                        "/claim #654\n\n"
                        "PR: https://github.com/ramimbo/mergework/pull/11"
                        "#pullrequestreview-4400000000\n"
                        f"Head SHA: `{current_head}`"
                    ),
                },
                {
                    "url": "https://github.com/ramimbo/mergework/issues/654#issuecomment-2",
                    "author": {"login": "reviewer-two"},
                    "createdAt": "2026-06-02T10:05:00Z",
                    "body": (
                        "/claim #654\n\n"
                        "PR #12\n"
                        f"Head SHA: `{stale_head}`\n"
                        f"Base/main SHA: `{'c' * 40}`"
                    ),
                },
                {
                    "url": "https://github.com/ramimbo/mergework/issues/654#issuecomment-3",
                    "author": {"login": "reviewer-three"},
                    "createdAt": "2026-06-02T10:10:00Z",
                    "body": (
                        "/claim #654\n\n"
                        "PR comment: https://github.com/ramimbo/mergework/pull/13"
                        "#issuecomment-4400000001"
                    ),
                },
            ],
        },
        reviewer="reviewer",
    )

    rows = {row["pull_request"]: row for row in report["pull_requests"]}

    assert rows[11]["state"] == "already_claimed_current_head"
    assert rows[11]["bounty_claims"][0]["evidence_type"] == "pr_review"
    assert rows[11]["bounty_claims"][0]["head_sha"] == current_head
    assert rows[12]["state"] == "claimed_stale_head_or_base"
    assert rows[12]["bounty_claims"][0]["head_sha"] == stale_head
    assert rows[13]["state"] == "claimed_by_pr_comment"
    assert rows[13]["bounty_claims"][0]["evidence_type"] == "pr_comment"
    assert rows[14]["state"] == "candidate_for_fresh_review"

    markdown = format_markdown_report(report)
    assert "issuecomment-1" in markdown
    assert "issuecomment-3" in markdown


def test_analyze_candidates_rejects_invalid_arguments() -> None:
    data = {"pull_requests": []}

    with pytest.raises(ValueError, match="reviewer"):
        analyze_candidates(data, reviewer="   ")
    with pytest.raises(ValueError, match="sufficient_reviews"):
        analyze_candidates(data, reviewer="reviewer", sufficient_reviews=0)


def test_review_bounty_candidate_reports_are_pasteable() -> None:
    report = analyze_candidates(
        {
            "pull_requests": [
                {
                    "number": 4,
                    "title": "Improve docs",
                    "url": "https://github.com/ramimbo/mergework/pull/4",
                    "author": {"login": "alice"},
                    "headRefOid": "h4",
                    "mergeStateStatus": "CLEAN",
                    "labels": [],
                    "statusCheckRollup": _quality_check(),
                    "reviews": [],
                }
            ]
        },
        reviewer="reviewer",
    )

    text = format_text_report(report)
    markdown = format_markdown_report(report)

    assert "Review bounty candidates for reviewer" in text
    assert "PR #4: candidate_for_fresh_review" in text
    assert "## Review Bounty Candidates For `reviewer`" in markdown
    assert "[PR #4](https://github.com/ramimbo/mergework/pull/4)" in markdown


def test_review_bounty_candidates_script_entrypoint_loads_parser() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/review_bounty_candidates.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout


def test_live_candidates_reports_missing_github_cli(monkeypatch) -> None:
    def missing_gh(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", missing_gh)

    with pytest.raises(RuntimeError, match="GitHub CLI executable 'gh' was not found"):
        load_live_candidates("ramimbo/mergework")


def test_live_candidates_can_join_paginated_bounty_claim_comments(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["gh", "pr", "list"]:
            stdout = json.dumps(
                [
                    {
                        "number": 41,
                        "title": "Open PR",
                        "url": "https://github.com/ramimbo/mergework/pull/41",
                        "author": {"login": "alice"},
                        "headRefOid": "a" * 40,
                        "mergeStateStatus": "CLEAN",
                        "labels": [],
                        "statusCheckRollup": _quality_check(),
                        "reviews": [],
                    }
                ]
            )
        elif args[:4] == ["gh", "api", "--paginate", "--slurp"]:
            stdout = json.dumps(
                [
                    [
                        {
                            "html_url": (
                                "https://github.com/ramimbo/mergework/issues/654#issuecomment-99"
                            ),
                            "user": {"login": "claimant"},
                            "created_at": "2026-06-02T10:00:00Z",
                            "body": (
                                "/claim #654\nPR: https://github.com/ramimbo/mergework/pull/41"
                            ),
                        }
                    ]
                ]
            )
        else:
            raise AssertionError(f"unexpected command: {args}")
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    data = load_live_candidates("ramimbo/mergework", bounty_issue=654)
    report = analyze_candidates(data, reviewer="reviewer")

    assert any(args[:4] == ["gh", "api", "--paginate", "--slurp"] for args in calls)
    assert report["pull_requests"][0]["state"] == "already_claimed_on_bounty_issue"
    assert report["pull_requests"][0]["bounty_claims"][0]["author"] == "claimant"
