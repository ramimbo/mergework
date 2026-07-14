from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.review_bounty_candidates import (
    _apply_bounty_saturation,
    analyze_candidates,
    format_markdown_report,
    format_text_report,
    index_bounty_claims,
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


@pytest.mark.parametrize(
    ("source_args", "expected_message"),
    (
        (["--input", ""], "--input must be a non-empty value"),
        (["--input", "   "], "--input must be a non-empty value"),
        (["--input", " tests/fixtures/missing.json "], "--input must not include"),
        (["--repo", ""], "--repo must be a non-empty value"),
        (["--repo", "   "], "--repo must be a non-empty value"),
        (["--repo", " ramimbo/mergework "], "--repo must not include"),
    ),
)
def test_review_bounty_candidates_rejects_empty_source_args(
    source_args: list[str],
    expected_message: str,
    capsys,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([*source_args, "--reviewer", "reviewer", "--format", "json"])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert expected_message in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


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


def _pr(
    number: int,
    *,
    head: str,
    base: str = "base1",
    merge_state: str = "CLEAN",
    reviews: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/ramimbo/mergework/pull/{number}",
        "author": {"login": "alice"},
        "headRefOid": head,
        "baseRefOid": base,
        "mergeStateStatus": merge_state,
        "labels": [],
        "statusCheckRollup": _quality_check(),
        "reviews": reviews or [],
    }


def test_review_bounty_candidates_marks_pr_review_claim_at_current_head() -> None:
    head = "c" * 40
    fixture = {
        "repo": "ramimbo/mergework",
        "pull_requests": [_pr(784, head=head)],
        "bounty_claim_comments": [
            {
                "html_url": "https://github.com/ramimbo/mergework/issues/654#issuecomment-1",
                "author": {"login": "bob"},
                "body": (
                    "Claiming review evidence "
                    "https://github.com/ramimbo/mergework/pull/784#pullrequestreview-4406541805\n"
                    f"head: {head}"
                ),
            }
        ],
    }

    report = analyze_candidates(fixture, reviewer="reviewer")
    row = report["pull_requests"][0]

    assert row["state"] == "already_claimed_current_head"
    assert row["claim_evidence_kind"] == "pr_review"
    assert row["matched_claim_urls"] == [
        "https://github.com/ramimbo/mergework/issues/654#issuecomment-1"
    ]


def test_review_bounty_candidates_marks_pr_comment_claim() -> None:
    fixture = {
        "repo": "ramimbo/mergework",
        "pull_requests": [_pr(533, head="head533")],
        "bounty_claim_comments": [
            {
                "html_url": "https://github.com/ramimbo/mergework/issues/654#issuecomment-2",
                "author": {"login": "bob"},
                "body": (
                    "Reviewed via concise PR comment "
                    "https://github.com/ramimbo/mergework/pull/533#issuecomment-999"
                ),
            }
        ],
    }

    report = analyze_candidates(fixture, reviewer="reviewer")
    row = report["pull_requests"][0]

    assert row["state"] == "claimed_by_pr_comment"
    assert row["claim_evidence_kind"] == "pr_comment"


def test_review_bounty_candidates_flags_stale_head_claim() -> None:
    fixture = {
        "repo": "ramimbo/mergework",
        "pull_requests": [_pr(662, head="newhead")],
        "bounty_claim_comments": [
            {
                "html_url": "https://github.com/ramimbo/mergework/issues/654#issuecomment-3",
                "author": {"login": "bob"},
                "body": (
                    "Claiming https://github.com/ramimbo/mergework/pull/662#pullrequestreview-1\n"
                    "headRefOid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                ),
            }
        ],
    }

    report = analyze_candidates(fixture, reviewer="reviewer")
    row = report["pull_requests"][0]

    assert row["state"] == "claimed_stale_head_or_base"
    assert "differs from current head" in row["reason"]


def test_review_bounty_candidates_flags_dirty_unclaimed_current_base_candidate() -> None:
    fixture = {
        "repo": "ramimbo/mergework",
        "pull_requests": [_pr(662, head="head662", merge_state="DIRTY")],
        "bounty_claim_comments": [],
    }

    report = analyze_candidates(fixture, reviewer="reviewer")
    row = report["pull_requests"][0]

    assert row["state"] == "dirty_unclaimed_current_base_candidate"


def test_review_bounty_candidates_claim_without_pr_review_object() -> None:
    fixture = {
        "repo": "ramimbo/mergework",
        "pull_requests": [_pr(784, head="head784", reviews=[])],
        "bounty_claim_comments": [
            {
                "html_url": "https://github.com/ramimbo/mergework/issues/654#issuecomment-4",
                "author": {"login": "bob"},
                "body": "Claiming PR #784 with evidence in bounty comment only.",
            }
        ],
    }

    report = analyze_candidates(fixture, reviewer="reviewer")
    row = report["pull_requests"][0]

    assert row["state"] == "already_claimed_on_bounty_issue"
    assert row["current_head_human_reviews"] == 0


def test_index_bounty_claims_parses_bare_pr_number() -> None:
    claims = index_bounty_claims(
        [
            {
                "html_url": "https://github.com/ramimbo/mergework/issues/654#issuecomment-5",
                "author": {"login": "bob"},
                "body": "Claiming review for PR #784",
            }
        ],
        repo="ramimbo/mergework",
    )

    assert 784 in claims
    assert claims[784][0]["evidence_kind"] == "pr_reference"


def test_review_bounty_candidates_live_mode_loads_bounty_issue_comments(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps([_pr(784, head="head784")]),
                stderr="",
            )
        if args[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps(
                    {
                        "comments": [
                            {
                                "html_url": "https://github.com/ramimbo/mergework/issues/654#issuecomment-1",
                                "author": {"login": "bob"},
                                "body": (
                                    "Claiming "
                                    "https://github.com/ramimbo/mergework/pull/784#pullrequestreview-1"
                                ),
                            }
                        ]
                    }
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    data = load_live_candidates("ramimbo/mergework", bounty_issue=654)
    report = analyze_candidates(data, reviewer="reviewer", repo=data["repo"])

    assert any(call[:3] == ["gh", "issue", "view"] for call in calls)
    assert report["pull_requests"][0]["state"] == "already_claimed_on_bounty_issue"


def test_review_bounty_candidates_rejects_bounty_issue_in_offline_mode(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--input",
                "fixture.json",
                "--bounty-issue",
                "654",
                "--reviewer",
                "reviewer",
            ]
        )

    assert exc_info.value.code == 2
    assert "--bounty-issue is only valid in live --repo mode" in capsys.readouterr().err


def test_apply_bounty_saturation_preserves_current_head_reviewer_state() -> None:
    row = {
        "state": "already_reviewed_current_head_by_reviewer",
        "reason": "reviewer already reviewed current head",
        "number": 784,
    }
    claims = [
        {
            "head_sha": "a" * 40,
            "base_sha": "b" * 40,
            "evidence_kind": "pr_body",
            "comment_url": "https://example.test/claim",
        }
    ]
    result = _apply_bounty_saturation(
        row,
        claims=claims,
        merge_state="clean",
        head_oid="a" * 40,
        base_oid="b" * 40,
    )
    assert result["state"] == "already_reviewed_current_head_by_reviewer"
