from __future__ import annotations

import json
import subprocess
import urllib.error
from typing import Any

from scripts.proposed_work_triage import _run_gh, analyze_proposed_work, format_markdown, main


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


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

## Evidence or tests required
Run the proposed-work triage tests.

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
        "tests",
        "duplicate_search",
        "out_of_scope",
    }


def test_proposed_work_triage_accepts_current_template_section_labels() -> None:
    body = """
## Problem
Maintainers need a clearer read-only view.

## Evidence
Issue comments show repeated confusion.

## Proposed work
Add a fixture-tested read-only report.

## Expected value
Maintainers can route work without guessing.

## Possible acceptance criteria
Focused tests and docs pass.

## Evidence or tests required
Run the proposed-work triage tests.

## Duplicate search
Searched open issues and did not find a duplicate.

## Out of scope
No labels, comments, payments, or bounty creation.
"""
    report = analyze_proposed_work(
        {
            "issues": [
                {
                    "number": 674,
                    "title": "Current template labels",
                    "body": body,
                    "labels": ["proposed-work"],
                    "comments": [],
                }
            ]
        }
    )

    assert report["proposals"][0]["missing_sections"] == []
    assert "missing_template_sections" not in report["proposals"][0]["warnings"]


def test_proposed_work_triage_accepts_short_tests_heading() -> None:
    body = """
## Problem
Maintainers need a clearer read-only view.

## Evidence
Issue comments show repeated confusion.

## Proposed work
Add a fixture-tested read-only report.

## Expected value
Maintainers can route work without guessing.

## Acceptance
Focused tests and docs pass.

## Tests
Run the proposed-work triage tests.

## Duplicate search
Searched open issues and did not find a duplicate.

## Out of scope
No labels, comments, payments, or bounty creation.
"""
    report = analyze_proposed_work(
        {
            "issues": [
                {
                    "number": 675,
                    "title": "Short tests heading",
                    "body": body,
                    "labels": ["proposed-work"],
                    "comments": [],
                }
            ]
        }
    )

    assert report["proposals"][0]["missing_sections"] == []
    assert "missing_template_sections" not in report["proposals"][0]["warnings"]


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
    related_warnings = {
        item["number"]: item["warnings"]
        for item in report["proposals"]
        if item["number"] in {680, 681}
    }
    assert all(
        "duplicate_looking_related_proposal" in warnings for warnings in related_warnings.values()
    )


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
                    "accepted_awards": [
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
            stdout = json.dumps([{"number": 672}, {"number": 673}])
        else:
            number = int(args[3])
            labels = [{"name": "proposed-work"}] if number == 672 else []
            stdout = json.dumps(
                {
                    "number": number,
                    "title": "Read-only proposed-work intake triage report",
                    "url": f"https://github.com/ramimbo/mergework/issues/{number}",
                    "body": _complete_body(),
                    "labels": labels,
                    "comments": [],
                }
            )
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    def fake_urlopen(request, timeout):  # noqa: ANN001, ANN202, ARG001
        url = request.full_url
        if url.endswith("/api/v1/bounties?issue_number=649&limit=5"):
            return FakeResponse([{"id": 96}])
        if url.endswith("/api/v1/bounties/96"):
            return FakeResponse(
                {
                    "id": 96,
                    "pending_payout_proposals": [
                        {
                            "proposal_id": 100,
                            "submission_url": "https://github.com/ramimbo/mergework/issues/672",
                            "accepted_by": "ramimbo",
                            "executes_after": "2026-06-01T18:44:46Z",
                        }
                    ],
                    "accepted_awards": [],
                }
            )
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr("scripts.proposed_work_triage.subprocess.run", fake_run)
    monkeypatch.setattr("scripts.proposed_work_triage.urllib.request.urlopen", fake_urlopen)

    assert main(["--repo", "ramimbo/mergework", "--format", "json", "--limit", "2"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["proposed_work_issues"] == 2
    assert output["summary"]["payment_counts"] == {"none": 1, "pending": 1}
    pending = next(item for item in output["proposals"] if item["number"] == 672)
    unlabeled = next(item for item in output["proposals"] if item["number"] == 673)
    assert "accepted_pending_payout" in pending["warnings"]
    assert "missing_proposed_work_label" in unlabeled["warnings"]
    assert all(
        call[:3] == ["gh", "issue", "list"] or call[:3] == ["gh", "issue", "view"] for call in calls
    )
    assert not any("comment" in call or "edit" in call for call in calls)


def test_proposed_work_triage_live_mode_warns_when_payment_state_is_incomplete(
    monkeypatch, capsys
) -> None:
    def fake_run(args, **kwargs):  # noqa: ANN001, ANN202
        if args[:3] == ["gh", "issue", "list"]:
            stdout = json.dumps([{"number": 672}])
        else:
            stdout = json.dumps(
                {
                    "number": 672,
                    "title": "Read-only proposed-work intake triage report",
                    "url": "https://github.com/ramimbo/mergework/issues/672",
                    "body": _complete_body(),
                    "labels": [{"name": "proposed-work"}],
                    "comments": [],
                }
            )
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    def fake_urlopen(request, timeout):  # noqa: ANN001, ANN202, ARG001
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("scripts.proposed_work_triage.subprocess.run", fake_run)
    monkeypatch.setattr("scripts.proposed_work_triage.urllib.request.urlopen", fake_urlopen)

    assert main(["--repo", "ramimbo/mergework", "--format", "json", "--limit", "1"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["summary"]["payment_counts"] == {"none": 1}
    assert output["summary"]["data_warnings"] == [
        "payment_state_incomplete: failed to load public bounty list for issue #649 (URLError)"
    ]
    markdown = format_markdown(output)
    assert "data warning: payment_state_incomplete" in markdown


def test_proposed_work_triage_live_mode_warns_when_bounty_detail_fetch_fails(
    monkeypatch, capsys
) -> None:
    def fake_run(args, **kwargs):  # noqa: ANN001, ANN202
        if args[:3] == ["gh", "issue", "list"]:
            stdout = json.dumps([{"number": 672}])
        else:
            stdout = json.dumps(
                {
                    "number": 672,
                    "title": "Read-only proposed-work intake triage report",
                    "url": "https://github.com/ramimbo/mergework/issues/672",
                    "body": _complete_body(),
                    "labels": [{"name": "proposed-work"}],
                    "comments": [],
                }
            )
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    def fake_urlopen(request, timeout):  # noqa: ANN001, ANN202, ARG001
        if request.full_url.endswith("/api/v1/bounties?issue_number=649&limit=5"):
            return FakeResponse([{"id": 96}])
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("scripts.proposed_work_triage.subprocess.run", fake_run)
    monkeypatch.setattr("scripts.proposed_work_triage.urllib.request.urlopen", fake_urlopen)

    assert main(["--repo", "ramimbo/mergework", "--format", "json", "--limit", "1"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["summary"]["payment_counts"] == {"none": 1}
    assert output["summary"]["data_warnings"] == [
        "payment_state_incomplete: failed to load public bounty "
        "detail for bounty 96; using list row only (URLError)"
    ]
    markdown = format_markdown(output)
    assert "data warning: payment_state_incomplete" in markdown


def test_run_gh_reports_timeout_and_invalid_json(monkeypatch) -> None:
    def fake_timeout(args, **kwargs):  # noqa: ANN001, ANN202, ARG001
        raise subprocess.TimeoutExpired(args, timeout=15)

    monkeypatch.setattr("scripts.proposed_work_triage.subprocess.run", fake_timeout)
    try:
        _run_gh(["issue", "list"])
    except RuntimeError as exc:
        assert "timed out" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected timeout RuntimeError")

    def fake_invalid_json(args, **kwargs):  # noqa: ANN001, ANN202
        return subprocess.CompletedProcess(args, 0, stdout="not-json", stderr="")

    monkeypatch.setattr("scripts.proposed_work_triage.subprocess.run", fake_invalid_json)
    try:
        _run_gh(["issue", "list"])
    except RuntimeError as exc:
        assert "invalid JSON" in str(exc)
        assert "not-json" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected invalid JSON RuntimeError")


def test_run_gh_rejects_mutating_commands_before_subprocess(monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG001
        raise AssertionError("mutating gh command should be rejected before subprocess.run")

    monkeypatch.setattr("scripts.proposed_work_triage.subprocess.run", fail_if_called)

    for args in (
        ["issue", "comment", "672", "--body", "mutation"],
        ["issue", "edit", "672", "--add-label", "proposed-work"],
        ["pr", "review", "763", "--approve"],
        ["api", "repos/ramimbo/mergework/issues/672"],
    ):
        try:
            _run_gh(args)
        except RuntimeError as exc:
            assert "only permits read-only gh commands" in str(exc)
        else:  # pragma: no cover - defensive assertion
            raise AssertionError(f"expected read-only guard for {args}")
