from __future__ import annotations

import json
from pathlib import Path

from scripts import flag_superseded_review_rounds as fsrr


def _issue(number: int, round_no: int, state: str = "open") -> dict:
    return {
        "number": number,
        "state": state,
        "title": (
            f"MRWK bounty: 40 MRWK - review open MergeWork PRs with evidence, round {round_no}"
        ),
        "labels": [{"name": "mrwk:bounty"}, {"name": "review"}],
    }


def test_classify_review_rounds_marks_older_rounds_superseded() -> None:
    issues = [_issue(643, 17), _issue(654, 18), _issue(838, 19), _issue(933, 20)]

    report = fsrr.classify_review_rounds(issues)

    assert report["current_issue_number"] == 933
    assert report["current_round"] == 20
    assert {row["issue_number"] for row in report["superseded"]} == {643, 654, 838}


def test_classify_review_rounds_does_not_flag_current_round() -> None:
    issues = [_issue(933, 20)]

    report = fsrr.classify_review_rounds(issues)

    assert report["superseded"] == []


def test_main_fixture_report(tmp_path: Path) -> None:
    payload = {"issues": [_issue(643, 17), _issue(933, 20)]}
    fixture = tmp_path / "issues.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")

    assert fsrr.main(["--input", str(fixture), "--fail-on-superseded"]) == 1


def test_render_report_names_current_round() -> None:
    report = fsrr.classify_review_rounds([_issue(643, 17), _issue(933, 20)])

    text = fsrr.render_report(report)

    assert "Current live review round: #933 (round 20)" in text
    assert "#643 round 17" in text


def test_classify_review_rounds_ignores_closed_newer_round() -> None:
    issues = [_issue(643, 17), _issue(933, 20, state="closed")]

    report = fsrr.classify_review_rounds(issues)

    assert report["current_issue_number"] == 643
    assert report["current_round"] == 17
    assert report["superseded"] == []
