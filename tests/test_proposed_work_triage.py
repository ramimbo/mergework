from __future__ import annotations

import json
import subprocess

from scripts.proposed_work_triage import analyze_proposed_work, format_markdown, main


def _complete_body(topic: str = "queue review") -> str:
    return f"""
## Problem
Maintainers need a clearer read-only view of {topic}.

## Evidence
Issue comments show repeated confusion around {topic}.

## Proposed work
Add a fixture-tested read-only report for {topic}.

## Expected value
Maintainers can route work without guessing.

## Acceptance
Focused tests and docs pass.

## Duplicate search
Searched open issues and did not find a duplicate.

## Out of scope
No labels, comments, payments, or bounty creation.
"""


def test_proposed_work_triage_accepts_complete_issue() -> None:
    report = analyze_proposed_work(
        {
            "issues": [
                {
                    "number": 672,
                    "title": "Read-only proposed-work intake triage report",
                    "url": "https://github.com/ramimbo/mergework/issues/672",
                    "body": _complete_body(),
                    "labels": [{"name": "proposed-work"}],
                    "comments": [],
                }
            ]
        }
    )

    assert report["summary"]["proposed_work_issues"] == 1
    assert report["proposals"][0]["warnings"] == []
    assert report["proposals"][0]["missing_sections"] == []


def test_proposed_work_triage_flags_missing_label_and_template_sections() -> None:
    report = analyze_proposed_work(
        {
            "issues": [
                {
                    "number": 673,
                    "title": "Maybe improve things",
                    "body": "Could be useful.",
                    "labels": [],
                    "comments": [],
                }
            ]
        }
    )

    proposal = report["proposals"][0]
    assert "missing_proposed_work_label" in proposal["warnings"]
    assert "missing_template_sections" in proposal["warnings"]
    assert "vague_or_under_specified" in proposal["warnings"]
    assert set(proposal["missing_sections"]) == {
        "problem",
        "evidence",
        "proposed_work",
        "value",
        "acceptance",
        "duplicate_search",
        "out_of_scope",
    }


def test_proposed_work_triage_groups_likely_related_proposals_conservatively() -> None:
    report = analyze_proposed_work(
        {
            "issues": [
                {
                    "number": 680,
                    "title": "Queue duplicate stale bounty triage report",
                    "body": _complete_body("queue duplicate stale bounty triage"),
                    "labels": ["proposed-work"],
                },
                {
                    "number": 681,
                    "title": "Queue duplicate stale bounty report",
                    "body": _complete_body("queue duplicate stale bounty report"),
                    "labels": ["proposed-work"],
                },
                {
                    "number": 682,
                    "title": "Wallet transfer nonce parser",
                    "body": _complete_body("wallet transfer nonce parsing"),
                    "labels": ["proposed-work"],
                },
            ]
        }
    )

    assert report["summary"]["related_groups"] == 1
    assert report["related_groups"][0]["issues"] == [680, 681]


def test_proposed_work_triage_separates_pending_and_paid_payment_status() -> None:
    report = analyze_proposed_work(
        {
            "issues": [
                {
                    "number": 687,
                    "title": "Accepted pending intake",
                    "url": "https://github.com/ramimbo/mergework/issues/687",
                    "body": _complete_body("accepted pending intake"),
                    "labels": ["proposed-work"],
                    "comments": [{"body": "Accepted and routed for maintainer review."}],
                },
                {
                    "number": 688,
                    "title": "Proof backed intake",
                    "url": "https://github.com/ramimbo/mergework/issues/688",
                    "body": _complete_body("proof backed intake"),
                    "labels": ["proposed-work"],
                },
            ],
            "bounties": [
                {
                    "issue_number": 649,
                    "pending_payout_proposals": [
                        {
                            "proposal_id": 96,
                            "submission_url": "https://github.com/ramimbo/mergework/issues/687",
                            "accepted_by": "ramimbo",
                            "executes_after": "2026-06-01T15:01:35Z",
                        }
                    ],
                    "awards": [
                        {
                            "submission_url": "https://github.com/ramimbo/mergework/issues/688",
                            "proof_url": "https://mrwk.online/proofs/example",
                            "ledger_sequence": 99,
                        }
                    ],
                }
            ],
        }
    )

    assert report["summary"]["payment_counts"] == {"paid": 1, "pending": 1}
    pending = next(item for item in report["proposals"] if item["number"] == 687)
    paid = next(item for item in report["proposals"] if item["number"] == 688)
    assert "accepted_pending_payout" in pending["warnings"]
    assert "proof_backed_paid" in paid["warnings"]
    assert "already_routed_or_accepted" in pending["warnings"]


def test_proposed_work_triage_flags_rejected_and_non_live_confused_proposals() -> None:
    report = analyze_proposed_work(
        {
            "issues": [
                {
                    "number": 690,
                    "title": "Confused claim proposal",
                    "body": _complete_body("confused claim proposal") + "\n/claim #694 now paid",
                    "labels": ["proposed-work"],
                    "comments": [{"body": "Rejected as out of scope."}],
                }
            ]
        }
    )

    warnings = set(report["proposals"][0]["warnings"])
    assert "rejected_or_out_of_scope" in warnings
    assert "non_live_bounty_confusion" in warnings


def test_proposed_work_triage_markdown_and_json_cli(tmp_path, capsys) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "number": 672,
                        "title": "Read-only proposed-work intake triage report",
                        "url": "https://github.com/ramimbo/mergework/issues/672",
                        "body": _complete_body(),
                        "labels": ["proposed-work"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert main(["--input", str(fixture), "--format", "json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["proposed_work_issues"] == 1

    markdown = format_markdown(output)
    assert "# Proposed Work Triage" in markdown
    assert "#672 Read-only proposed-work intake triage report" in markdown


def test_proposed_work_triage_live_mode_uses_read_only_gh(monkeypatch, capsys) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):  # noqa: ANN001, ANN202
        calls.append(args)
        if args[:3] == ["gh", "issue", "list"]:
            stdout = json.dumps([{"number": 672}])
        else:
            stdout = json.dumps(
                {
                    "number": 672,
                    "title": "Read-only proposed-work intake triage report",
                    "body": _complete_body(),
                    "labels": [{"name": "proposed-work"}],
                    "comments": [],
                }
            )
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("scripts.proposed_work_triage.subprocess.run", fake_run)

    assert main(["--repo", "ramimbo/mergework", "--format", "json", "--limit", "1"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["proposed_work_issues"] == 1
    assert all(
        call[:3] == ["gh", "issue", "list"] or call[:3] == ["gh", "issue", "view"] for call in calls
    )
    assert not any("comment" in call or "edit" in call for call in calls)
