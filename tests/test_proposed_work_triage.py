from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import proposed_work_triage
from scripts.proposed_work_triage import analyze_proposed_work, format_markdown_report, main

ROOT = Path(__file__).resolve().parents[1]


def _body(
    *,
    problem: str = "Maintainers need a clearer way to inspect this public queue.",
    evidence: str = "https://github.com/ramimbo/mergework/issues/700 shows the current behavior.",
    proposed_work: str = "Add a small read-only report with fixture coverage.",
    expected_value: str = "Maintainers can triage related proposals without changing public state.",
    acceptance: str = "The report lists the issue and flags only public evidence.",
    tests: str = "Run pytest for the focused fixture tests.",
    duplicate_search: str = "No existing report covers this exact scope.",
    out_of_scope: str = "No labels, comments, payments, or bounty creation.",
) -> str:
    return f"""### Problem
{problem}

### Evidence
{evidence}

### Proposed work
{proposed_work}

### Expected value
{expected_value}

### Possible acceptance criteria
{acceptance}

### Evidence or tests required
{tests}

### Duplicate search
{duplicate_search}

### Out of scope
{out_of_scope}
"""


def _issue(
    number: int,
    title: str,
    *,
    body: str | None = None,
    labels: list[str] | None = None,
    comments: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/ramimbo/mergework/issues/{number}",
        "author": {"login": f"author{number}"},
        "labels": labels if labels is not None else ["proposed-work"],
        "body": body if body is not None else _body(evidence=f"https://example.test/{number}"),
        "comments": comments or [],
    }


def _fixture() -> dict[str, object]:
    return {
        "bounties": [
            {
                "id": 96,
                "issue_number": 649,
                "pending_payout_proposals": [
                    {
                        "proposal_id": 101,
                        "submission_url": "https://github.com/ramimbo/mergework/issues/3",
                        "executes_after": "2026-06-01T18:44:46Z",
                    }
                ],
            }
        ],
        "activity": {
            "recent": [
                {
                    "submission_url": "https://github.com/ramimbo/mergework/issues/4",
                    "proof_url": "/proofs/paid-intake",
                    "bounty_issue_number": 649,
                }
            ]
        },
        "issues": [
            _issue(1, "Proposed work: complete fixture report"),
            _issue(2, "Proposed work: vague request", body="### Problem\nTBD\n"),
            _issue(
                3,
                "Proposed work: already routed intake",
                comments=[
                    {
                        "author": {"login": "ramimbo"},
                        "body": (
                            "Maintainer intake pass: routed to a separate implementation bounty."
                        ),
                    }
                ],
            ),
            _issue(4, "Proposed work: paid intake"),
            _issue(
                5,
                "Proposed work: add MCP schema report",
                labels=[],
                body=_body(evidence="https://github.com/ramimbo/mergework/issues/710"),
            ),
            _issue(
                6,
                "Proposed work: add MCP schema checks",
                body=_body(evidence="https://github.com/ramimbo/mergework/issues/710"),
            ),
            _issue(
                7,
                "Proposed work: rejected scope",
                labels=["proposed-work", "mrwk:rejected"],
            ),
            _issue(
                8,
                "Proposed work: confused live claim",
                body=_body(problem="This is claimable now and I will /claim #694 for it."),
            ),
        ],
    }


def test_proposed_work_triage_classifies_fixture(tmp_path, capsys) -> None:
    report = analyze_proposed_work(_fixture(), api_host="https://api.example.test")
    rows = {row["issue"]: row for row in report["rows"]}

    assert rows[1]["warnings"] == []
    assert rows[2]["warnings"] == ["missing_template_sections", "vague"]
    assert rows[3]["intake_status"] == "pending_payout"
    assert rows[3]["pending_proposal_url"] == (
        "https://api.example.test/api/v1/treasury/proposals/101"
    )
    assert rows[3]["pending_executes_after"] == "2026-06-01T18:44:46Z"
    assert "already_routed" in rows[3]["warnings"]
    assert rows[4]["intake_status"] == "paid"
    assert rows[4]["intake_proof_url"] == "https://api.example.test/proofs/paid-intake"
    assert "already_routed" in rows[4]["warnings"]
    assert rows[5]["warnings"] == ["duplicate_looking", "label_missing"]
    assert rows[5]["related_group"] == rows[6]["related_group"]
    assert "duplicate_looking" in rows[6]["warnings"]
    assert rows[7]["warnings"] == ["rejected"]
    assert "non_live_confused" in rows[8]["warnings"]
    assert report["summary"]["paid_intake"] == 1
    assert report["summary"]["pending_intake"] == 1
    assert report["summary"]["missing_label"] == 1
    assert report["summary"]["suggested_consolidations"] == 1
    assert report["suggested_bounty_scopes"][0]["issues"] == [5, 6]

    input_path = tmp_path / "proposed-work.json"
    input_path.write_text(json.dumps(_fixture()), encoding="utf-8")
    assert main(["--input", str(input_path), "--format", "json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["proposed_work_issues"] == 8


def test_proposed_work_triage_markdown_report_is_pasteable() -> None:
    markdown = format_markdown_report(
        analyze_proposed_work(_fixture(), api_host="https://api.example.test")
    )

    assert "## Proposed Work Intake Triage" in markdown
    assert "| Issue | Intake | Warnings | Related |" in markdown
    assert "[paid](https://api.example.test/proofs/paid-intake)" in markdown
    assert "[pending payout](https://api.example.test/api/v1/treasury/proposals/101)" in markdown
    assert "`duplicate_looking`" in markdown
    assert "### Suggested Consolidations" in markdown


def test_proposed_work_triage_live_mode_uses_read_only_calls(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run_gh_json(args: list[str]) -> object:
        calls.append(args)
        if args[:3] == ["gh", "issue", "list"]:
            return [
                {
                    "number": 10,
                    "title": "Proposed work: labeled request",
                    "url": "https://github.com/ramimbo/mergework/issues/10",
                    "labels": [{"name": "proposed-work"}],
                    "author": {"login": "alice"},
                },
                {
                    "number": 11,
                    "title": "Proposed work: missing label request",
                    "url": "https://github.com/ramimbo/mergework/issues/11",
                    "labels": [],
                    "author": {"login": "bob"},
                },
                {
                    "number": 12,
                    "title": "Regular issue",
                    "url": "https://github.com/ramimbo/mergework/issues/12",
                    "labels": [],
                    "author": {"login": "carol"},
                },
            ]
        if args[:3] == ["gh", "issue", "view"]:
            number = int(args[3])
            return _issue(
                number,
                f"Proposed work: fixture {number}",
                labels=["proposed-work"] if number == 10 else [],
            )
        raise AssertionError(args)

    monkeypatch.setattr(proposed_work_triage, "_run_gh_json", fake_run_gh_json)
    monkeypatch.setattr(
        proposed_work_triage,
        "load_public_api_state",
        lambda api_host: {"bounties": [], "activity": {"recent": []}},
    )

    data = proposed_work_triage.load_live_triage("ramimbo/mergework", "https://api.example.test")
    report = analyze_proposed_work(data)

    assert [issue["number"] for issue in data["issues"]] == [10, 11]
    assert report["summary"]["missing_label"] == 1
    assert calls
    assert all(call[:3] in (["gh", "issue", "list"], ["gh", "issue", "view"]) for call in calls)


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


def test_admin_runbook_documents_proposed_work_triage() -> None:
    runbook = (ROOT / "docs" / "admin-runbook.md").read_text(encoding="utf-8")

    assert "scripts/proposed_work_triage.py" in runbook
    assert "read-only proposed-work intake triage" in runbook
    assert "--input proposed-work-triage.json" in runbook
