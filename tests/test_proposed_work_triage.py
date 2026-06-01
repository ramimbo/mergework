from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import proposed_work_triage
from scripts.proposed_work_triage import (
    analyze_proposed_work,
    format_markdown_report,
    load_live_triage,
    main,
)

ROOT = Path(__file__).resolve().parents[1]


def _body(
    *,
    problem: str = (
        "The intake queue has multiple proposals and maintainers need a clear read-only summary."
    ),
    evidence: str = "See issue #649, issue #650, and the public proposed-work queue for examples.",
    proposed: str = (
        "Add a read-only report that classifies proposals without changing GitHub state."
    ),
    value: str = (
        "Maintainers can see complete, incomplete, routed, rejected, pending, and paid work faster."
    ),
    acceptance: str = (
        "The report lists each proposal, readiness gaps, routed bounties, and "
        "recommended next action."
    ),
    tests: str = "Run pytest for fixture mode and a docs smoke check for the runbook entry.",
    duplicate: str = (
        "Searched related proposal issues and no live bounty currently covers this exact scope."
    ),
    out_of_scope: str = (
        "No bounty creation, comments, labels, payout execution, or private API mutation."
    ),
) -> str:
    return f"""### Problem
{problem}

### Evidence
{evidence}

### Proposed work
{proposed}

### Expected value
{value}

### Possible acceptance criteria
{acceptance}

### Evidence or tests required
{tests}

### Duplicate search
{duplicate}

### Out of scope
{out_of_scope}
"""


def _fixture() -> dict[str, object]:
    return {
        "bounties": [
            {
                "id": 90,
                "issue_number": 649,
                "pending_payout_proposals": [
                    {
                        "proposal_id": 60,
                        "submission_url": (
                            "https://github.com/ramimbo/mergework/issues/15#issuecomment-1"
                        ),
                        "executes_after": "2026-06-01T11:41:45Z",
                    }
                ],
            }
        ],
        "proofs": [
            {
                "source_url": "https://github.com/ramimbo/mergework/issues/16",
                "proof_url": "https://api.example.test/proofs/paid-16",
            }
        ],
        "issues": [
            {
                "number": 10,
                "title": "Proposed work: intake queue summary",
                "url": "https://github.com/ramimbo/mergework/issues/10",
                "state": "OPEN",
                "labels": ["proposed-work"],
                "author": {"login": "alice"},
                "body": _body(),
                "comments": [],
            },
            {
                "number": 11,
                "title": "Proposed work: vague idea",
                "url": "https://github.com/ramimbo/mergework/issues/11",
                "state": "OPEN",
                "labels": ["proposed-work"],
                "author": {"login": "bob"},
                "body": "### Problem\nBug\n\n### Evidence\nTBD\n",
                "comments": [],
            },
            {
                "number": 12,
                "title": "Proposed work: missing label report",
                "url": "https://github.com/ramimbo/mergework/issues/12",
                "state": "OPEN",
                "labels": [],
                "author": {"login": "carol"},
                "body": _body(),
                "comments": [],
            },
            {
                "number": 13,
                "title": "Proposed work: routed report",
                "url": "https://github.com/ramimbo/mergework/issues/13",
                "state": "OPEN",
                "labels": ["proposed-work"],
                "author": {"login": "dave"},
                "body": _body(),
                "comments": [
                    {
                        "url": "https://github.com/ramimbo/mergework/issues/13#issuecomment-1",
                        "body": "Routed to Bounty #694. Reserved on MergeWork once executed.",
                    }
                ],
            },
            {
                "number": 14,
                "title": "Proposed work: rejected bridge copy",
                "url": "https://github.com/ramimbo/mergework/issues/14",
                "state": "CLOSED",
                "stateReason": "NOT_PLANNED",
                "labels": ["proposed-work"],
                "author": {"login": "erin"},
                "body": _body(),
                "comments": [],
            },
            {
                "number": 15,
                "title": "Proposed work: pending payout intake",
                "url": "https://github.com/ramimbo/mergework/issues/15",
                "state": "OPEN",
                "labels": ["proposed-work"],
                "author": {"login": "frank"},
                "body": _body(),
                "comments": [
                    {
                        "url": "https://github.com/ramimbo/mergework/issues/15#issuecomment-1",
                        "body": "Accepted for review; pending proposal only, not paid yet.",
                    }
                ],
            },
            {
                "number": 16,
                "title": "Proposed work: paid proof intake",
                "url": "https://github.com/ramimbo/mergework/issues/16",
                "state": "OPEN",
                "labels": ["proposed-work"],
                "author": {"login": "grace"},
                "body": _body(),
                "comments": [],
            },
            {
                "number": 17,
                "title": "Proposed work: wallet payout reconciliation report",
                "url": "https://github.com/ramimbo/mergework/issues/17",
                "state": "OPEN",
                "labels": ["proposed-work"],
                "author": {"login": "heidi"},
                "body": _body(),
                "comments": [],
            },
            {
                "number": 18,
                "title": "Proposed work: wallet payout reconciliation checker",
                "url": "https://github.com/ramimbo/mergework/issues/18",
                "state": "OPEN",
                "labels": ["proposed-work"],
                "author": {"login": "ivy"},
                "body": _body(),
                "comments": [],
            },
        ],
    }


def test_proposed_work_triage_classifies_required_cases(tmp_path, capsys) -> None:
    report = analyze_proposed_work(_fixture(), api_host="https://api.example.test")
    rows = {row["number"]: row for row in report["issues"]}

    assert report["summary"] == {
        "issues_scanned": 9,
        "proposed_work_issues": 9,
        "active": 7,
        "routed": 1,
        "rejected": 1,
        "complete": 8,
        "incomplete": 1,
        "label_missing": 1,
        "proof_backed_paid": 1,
        "pending_payout": 1,
        "related_groups": 1,
    }
    assert rows[10]["readiness"] == "complete"
    assert rows[11]["readiness"] == "incomplete"
    assert "Evidence" in rows[11]["weak_sections"]
    assert "Proposed work" in rows[11]["missing_sections"]
    assert rows[12]["warnings"] == ["missing proposed-work label"]
    assert rows[13]["classification"] == "routed"
    assert rows[13]["routed_refs"] == [694]
    assert rows[14]["classification"] == "rejected"
    assert rows[15]["payment_status"] == "pending_payout"
    assert (
        rows[15]["pending_proposal_url"] == "https://api.example.test/api/v1/treasury/proposals/60"
    )
    assert rows[16]["payment_status"] == "proof_backed_paid"
    assert rows[16]["payment_url"] == "https://api.example.test/proofs/paid-16"
    assert report["related_groups"][0]["issues"] == [
        {
            "number": 17,
            "title": "Proposed work: wallet payout reconciliation report",
            "url": "https://github.com/ramimbo/mergework/issues/17",
        },
        {
            "number": 18,
            "title": "Proposed work: wallet payout reconciliation checker",
            "url": "https://github.com/ramimbo/mergework/issues/18",
        },
    ]

    input_path = tmp_path / "proposed-work.json"
    input_path.write_text(json.dumps(_fixture()), encoding="utf-8")
    exit_code = main(["--input", str(input_path), "--format", "json", "--fail-on-warnings"])
    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["pending_payout"] == 1


def test_proposed_work_triage_markdown_is_pasteable() -> None:
    report = analyze_proposed_work({"issues": [_fixture()["issues"][0]]})
    markdown = format_markdown_report(report)

    assert "## Proposed Work Intake Triage" in markdown
    assert "| Issue | State | Status | Payment | Action |" in markdown
    assert "Ready for maintainer intake review" in markdown


def test_proposed_work_triage_script_entrypoint_loads_shared_parser() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/proposed_work_triage.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout


def test_live_triage_uses_only_read_only_gh_commands(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(args: list[str]) -> object:
        commands.append(args)
        if args[:3] == ["gh", "issue", "list"]:
            if "--label" in args:
                return [
                    {
                        "number": 22,
                        "title": "Proposed work: live report",
                        "url": "https://github.com/ramimbo/mergework/issues/22",
                        "state": "OPEN",
                        "labels": [{"name": "proposed-work"}],
                        "author": {"login": "agent"},
                    }
                ]
            return []
        if args[:3] == ["gh", "issue", "view"]:
            return {
                "number": 22,
                "title": "Proposed work: live report",
                "url": "https://github.com/ramimbo/mergework/issues/22",
                "state": "OPEN",
                "labels": [{"name": "proposed-work"}],
                "author": {"login": "agent"},
                "body": _body(),
                "comments": [],
            }
        raise AssertionError(args)

    monkeypatch.setattr(proposed_work_triage, "_run_gh_json", fake_run)

    data = load_live_triage("ramimbo/mergework", include_public_api=False)

    assert data["issues"][0]["number"] == 22
    assert all(
        command[:3] in (["gh", "issue", "list"], ["gh", "issue", "view"]) for command in commands
    )
    disallowed = proposed_work_triage.MUTATING_GH_WORDS
    assert not any(word in command for command in commands for word in disallowed)


def test_read_only_gh_guard_rejects_api_field_flags() -> None:
    for flag in (
        *proposed_work_triage.MUTATING_GH_API_FIELD_FLAGS,
        "--field=title=test",
        "--raw-field=title=test",
        "-ftitle=test",
        "-Ftitle=test",
    ):
        with pytest.raises(RuntimeError, match="field mutation flags"):
            args = ["gh", "api", "repos/ramimbo/mergework/issues", flag]
            if "=" not in flag:
                args.append("title=test")
            proposed_work_triage._assert_read_only_gh(args)


def test_read_only_gh_guard_rejects_non_get_methods() -> None:
    for method in ("POST", "PATCH", "PUT", "DELETE"):
        for args in (
            ["gh", "api", "repos/ramimbo/mergework/issues", "-X", method],
            ["gh", "api", "repos/ramimbo/mergework/issues", f"-X{method}"],
            ["gh", "api", "repos/ramimbo/mergework/issues", f"--method={method}"],
        ):
            with pytest.raises(RuntimeError, match="non-read-only gh api"):
                proposed_work_triage._assert_read_only_gh(args)


def test_read_only_gh_guard_allows_get_and_head_methods() -> None:
    for args in (
        ["gh", "api", "repos/ramimbo/mergework/issues", "-X", "GET"],
        ["gh", "api", "repos/ramimbo/mergework/issues", "-XHEAD"],
        ["gh", "api", "repos/ramimbo/mergework/issues", "--method=GET"],
    ):
        proposed_work_triage._assert_read_only_gh(args)


def test_live_triage_merges_public_paid_and_pending_state(monkeypatch) -> None:
    def fake_run(args: list[str]) -> object:
        if args[:3] == ["gh", "issue", "list"]:
            if "--label" in args:
                return [
                    {
                        "number": 23,
                        "title": "Proposed work: pending public payment state",
                        "url": "https://github.com/ramimbo/mergework/issues/23",
                        "state": "OPEN",
                        "labels": [{"name": "proposed-work"}],
                        "author": {"login": "agent"},
                    },
                    {
                        "number": 24,
                        "title": "Proposed work: paid public payment state",
                        "url": "https://github.com/ramimbo/mergework/issues/24",
                        "state": "OPEN",
                        "labels": [{"name": "proposed-work"}],
                        "author": {"login": "agent"},
                    },
                ]
            return []
        if args[:3] == ["gh", "issue", "view"]:
            number = int(args[3])
            comments = []
            if number == 23:
                comments = [
                    {
                        "url": "https://github.com/ramimbo/mergework/issues/23#issuecomment-1",
                        "body": "/claim #649 pending intake evidence",
                    }
                ]
            return {
                "number": number,
                "title": f"Proposed work: live {number}",
                "url": f"https://github.com/ramimbo/mergework/issues/{number}",
                "state": "OPEN",
                "labels": [{"name": "proposed-work"}],
                "author": {"login": "agent"},
                "body": _body(),
                "comments": comments,
            }
        raise AssertionError(args)

    def fake_get_json(url: str) -> object:
        if url.endswith("/api/v1/bounties?limit=200"):
            return [
                {
                    "id": 97,
                    "issue_number": 649,
                    "pending_payout_proposals": [
                        {
                            "proposal_id": 77,
                            "submission_url": (
                                "https://github.com/ramimbo/mergework/issues/23#issuecomment-1"
                            ),
                        }
                    ],
                }
            ]
        if url.endswith("/api/v1/activity?limit=200"):
            return {
                "recent": [
                    {
                        "submission_url": "https://github.com/ramimbo/mergework/issues/24",
                        "proof_url": "/proofs/paid-24",
                    }
                ],
                "contributors": [],
            }
        raise AssertionError(url)

    monkeypatch.setattr(proposed_work_triage, "_run_gh_json", fake_run)
    monkeypatch.setattr(proposed_work_triage, "_get_json", fake_get_json)

    data = load_live_triage("ramimbo/mergework", api_host="https://api.example.test")
    report = analyze_proposed_work(data, api_host="https://api.example.test")
    rows = {row["number"]: row for row in report["issues"]}

    assert rows[23]["payment_status"] == "pending_payout"
    assert (
        rows[23]["pending_proposal_url"] == "https://api.example.test/api/v1/treasury/proposals/77"
    )
    assert rows[24]["payment_status"] == "proof_backed_paid"
    assert rows[24]["payment_url"] == "https://api.example.test/proofs/paid-24"


def test_public_payment_state_rejects_unexpected_bounties_shape(monkeypatch) -> None:
    def fake_get_json(url: str) -> object:
        if url.endswith("/api/v1/bounties?limit=200"):
            return {"bounties": []}
        if url.endswith("/api/v1/activity?limit=200"):
            return {"recent": [], "contributors": []}
        raise AssertionError(url)

    monkeypatch.setattr(proposed_work_triage, "_get_json", fake_get_json)

    with pytest.raises(RuntimeError, match="/api/v1/bounties response shape"):
        proposed_work_triage.load_public_payment_state("https://api.example.test")


def test_public_payment_state_rejects_unexpected_activity_shape(monkeypatch) -> None:
    def fake_get_json(url: str) -> object:
        if url.endswith("/api/v1/bounties?limit=200"):
            return []
        if url.endswith("/api/v1/activity?limit=200"):
            return []
        raise AssertionError(url)

    monkeypatch.setattr(proposed_work_triage, "_get_json", fake_get_json)

    with pytest.raises(RuntimeError, match="/api/v1/activity response shape"):
        proposed_work_triage.load_public_payment_state("https://api.example.test")


def test_repo_fixture_offline_input(capsys) -> None:
    fixture_path = ROOT / "tests" / "fixtures" / "proposed_work_triage.json"
    exit_code = main(["--input", str(fixture_path), "--format", "markdown"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Proposed Work Intake Triage" in output
    assert "pending payout" in output.lower()
