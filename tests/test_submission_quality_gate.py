from __future__ import annotations

import json
import subprocess

from scripts import submission_quality_gate
from scripts.submission_quality_gate import evaluate_submission, main


def test_submission_quality_gate_passes_open_bounty_with_evidence(capsys, tmp_path) -> None:
    fixture = {
        "submission_text": """
        Summary:
        Add a focused pre-submission gate for agents.

        Refs #319

        Validation:
        - python -m pytest tests/test_submission_quality_gate.py -q -> 5 passed.
        """,
        "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 2}],
        "pull_requests": [],
    }

    result = evaluate_submission(fixture)

    assert result["status"] == "pass"
    assert result["bounty_reference"] == 319
    assert {check["name"]: check["status"] for check in result["checks"]} == {
        "bounty_reference": "pass",
        "bounty_payable": "pass",
        "summary_present": "pass",
        "evidence_present": "pass",
        "public_safety": "pass",
        "similar_open_pr": "pass",
    }

    input_path = tmp_path / "submission.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8")
    assert main(["--input", str(input_path), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


def test_submission_quality_gate_fails_missing_reference() -> None:
    result = evaluate_submission(
        {
            "submission_text": "Summary: add validation\n\nValidation: pytest passed",
            "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [],
        }
    )

    assert result["status"] == "fail"
    assert result["checks"][0] == {
        "name": "bounty_reference",
        "status": "fail",
        "message": "submission text must include Bounty #<issue> or Refs #<issue>",
    }


def test_submission_quality_gate_fails_closed_or_exhausted_bounty() -> None:
    result = evaluate_submission(
        {
            "submission_text": (
                "Summary: add validation\n\nBounty #319\n\nValidation: pytest passed"
            ),
            "bounties": [{"number": 319, "state": "CLOSED", "awards_remaining": 0}],
            "pull_requests": [],
        }
    )

    assert result["status"] == "fail"
    assert {
        "name": "bounty_payable",
        "status": "fail",
        "message": "referenced bounty #319 is closed or exhausted",
    } in result["checks"]


def test_submission_quality_gate_warns_for_missing_evidence() -> None:
    result = evaluate_submission(
        {
            "submission_text": "Summary: add validation\n\nRefs #319",
            "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [],
        }
    )

    assert result["status"] == "warn"
    assert {
        "name": "evidence_present",
        "status": "warn",
        "message": "include concrete test or validation evidence before submission",
    } in result["checks"]


def test_submission_quality_gate_warns_for_similar_open_pr() -> None:
    result = evaluate_submission(
        {
            "submission_text": """
            Summary: Add agent submission quality gate.
            Refs #319
            Validation: pytest passed.
            """,
            "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [
                {
                    "number": 12,
                    "title": "Add agent submission quality gate",
                    "body": "Refs #319",
                    "state": "OPEN",
                    "url": "https://github.com/ramimbo/mergework/pull/12",
                }
            ],
        }
    )

    assert result["status"] == "warn"
    assert result["similar_open_prs"] == [
        {
            "number": 12,
            "title": "Add agent submission quality gate",
            "url": "https://github.com/ramimbo/mergework/pull/12",
        }
    ]


def test_submission_quality_gate_fails_private_key_in_submission_text() -> None:
    result = evaluate_submission(
        {
            "submission_text": """
            Summary: add validation.
            Refs #319
            Validation: pytest passed.

            DEPLOYMENT_PRIVATE_KEY=abc123
            """,
            "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [],
        }
    )

    assert result["status"] == "fail"
    assert {
        "name": "public_safety",
        "status": "fail",
        "message": "submission text appears to include restricted secrets or credentials",
    } in result["checks"]


def test_submission_quality_gate_warns_for_mrwk_price_claims() -> None:
    result = evaluate_submission(
        {
            "submission_text": """
            Summary: add validation.
            Refs #319
            Validation: pytest passed.

            This proves MRWK will be worth $5 soon.
            """,
            "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [],
        }
    )

    assert result["status"] == "warn"
    assert {
        "name": "public_safety",
        "status": "warn",
        "message": "avoid MRWK price, payout, or investment claims in public artifacts",
    } in result["checks"]


def test_submission_quality_gate_passes_recent_maintainer_activity() -> None:
    result = evaluate_submission(
        {
            "submission_text": "Summary: add validation\n\nRefs #319\n\nValidation: pytest passed",
            "now": "2026-05-26T12:00:00Z",
            "bounties": [
                {
                    "number": 319,
                    "state": "OPEN",
                    "awards_remaining": 1,
                    "last_maintainer_activity_at": "2026-05-25T12:00:00Z",
                    "maintainer_activity_verified": True,
                    "max_maintainer_age_days": 14,
                }
            ],
            "pull_requests": [],
        }
    )

    assert result["status"] == "pass"
    assert {
        "name": "maintainer_activity",
        "status": "pass",
        "message": "maintainer activity for bounty #319 was seen 1 days ago",
    } in result["checks"]


def test_submission_quality_gate_warns_for_stale_maintainer_activity() -> None:
    result = evaluate_submission(
        {
            "submission_text": "Summary: add validation\n\nRefs #319\n\nValidation: pytest passed",
            "now": "2026-05-26T12:00:00Z",
            "bounties": [
                {
                    "number": 319,
                    "state": "OPEN",
                    "awards_remaining": 1,
                    "last_maintainer_activity_at": "2026-04-01T12:00:00Z",
                    "maintainer_activity_verified": True,
                    "max_maintainer_age_days": 14,
                }
            ],
            "pull_requests": [],
        }
    )

    assert result["status"] == "warn"
    assert {
        "name": "maintainer_activity",
        "status": "warn",
        "message": "last maintainer activity for bounty #319 was 55 days ago",
    } in result["checks"]


def test_submission_quality_gate_warns_when_activity_exceeds_threshold_by_seconds() -> None:
    result = evaluate_submission(
        {
            "submission_text": "Summary: add validation\n\nRefs #319\n\nValidation: pytest passed",
            "now": "2026-05-16T12:00:01Z",
            "bounties": [
                {
                    "number": 319,
                    "state": "OPEN",
                    "awards_remaining": 1,
                    "last_maintainer_activity_at": "2026-05-02T12:00:00Z",
                    "maintainer_activity_verified": True,
                    "max_maintainer_age_days": 14,
                }
            ],
            "pull_requests": [],
        }
    )

    assert result["status"] == "warn"
    assert {
        "name": "maintainer_activity",
        "status": "warn",
        "message": "last maintainer activity for bounty #319 was 14 days ago",
    } in result["checks"]


def test_submission_quality_gate_cli_returns_failure_exit(capsys, tmp_path) -> None:
    fixture = {
        "submission_text": "Summary: missing reference\n\nValidation: pytest passed",
        "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
        "pull_requests": [],
    }
    input_path = tmp_path / "submission.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8")

    assert main(["--input", str(input_path), "--format", "json"]) == 1

    assert json.loads(capsys.readouterr().out)["status"] == "fail"


def test_submission_quality_gate_live_mode_warns_when_github_unavailable(
    monkeypatch, capsys, tmp_path
) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["gh", "pr", "list"], timeout=30)

    monkeypatch.setattr(submission_quality_gate.subprocess, "run", fake_run)
    text_path = tmp_path / "draft.md"
    text_path.write_text(
        "Summary: work\n\nRefs #319\n\nValidation: pytest passed",
        encoding="utf-8",
    )

    assert main(["--text-file", str(text_path), "--format", "json"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "warn"
    assert "load_warning" in output


def test_submission_quality_gate_live_bounties_use_api_award_capacity(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        if args[:3] == ["gh", "issue", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps([{"number": 319, "title": "MRWK bounty: gate", "state": "OPEN"}]),
                stderr="",
            )
        if args[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps({"createdAt": "2026-05-20T00:00:00Z", "comments": []}),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(submission_quality_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        submission_quality_gate,
        "_load_api_bounties",
        lambda repo, api_host: {
            319: {
                "number": 319,
                "state": "OPEN",
                "awards_remaining": 0,
            }
        },
    )

    data = submission_quality_gate._load_live_context(
        "ramimbo/mergework",
        "Summary: work\n\nRefs #319\n\nValidation: pytest passed",
        "https://api.example.test",
    )
    result = evaluate_submission(data)

    assert result["status"] == "fail"
    assert {
        "name": "bounty_payable",
        "status": "fail",
        "message": "referenced bounty #319 is closed or exhausted",
    } in result["checks"]


def test_submission_quality_gate_live_context_adds_maintainer_activity(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        if args[:3] == ["gh", "issue", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps([{"number": 319, "title": "MRWK bounty: gate", "state": "OPEN"}]),
                stderr="",
            )
        if args[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps(
                    {
                        "author": {"login": "ramimbo"},
                        "createdAt": "2026-05-20T00:00:00Z",
                        "comments": [
                            {
                                "authorAssociation": "OWNER",
                                "createdAt": "2026-05-25T12:00:00Z",
                            },
                            {
                                "authorAssociation": "CONTRIBUTOR",
                                "createdAt": "2026-05-25T13:00:00Z",
                            },
                        ],
                    }
                ),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(submission_quality_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        submission_quality_gate,
        "_load_api_bounties",
        lambda repo, api_host: {319: {"number": 319, "state": "OPEN", "awards_remaining": 1}},
    )

    data = submission_quality_gate._load_live_context(
        "ramimbo/mergework",
        "Summary: work\n\nRefs #319\n\nValidation: pytest passed",
        "https://api.example.test",
        14,
    )
    data["now"] = "2026-05-26T12:00:00Z"

    assert data["bounties"][0]["maintainer_activity_verified"] is True
    assert data["bounties"][0]["last_maintainer_activity_at"] == "2026-05-25T12:00:00Z"

    result = evaluate_submission(data)
    assert result["status"] == "pass"
    assert {
        "name": "maintainer_activity",
        "status": "pass",
        "message": "maintainer activity for bounty #319 was seen 1 days ago",
    } in result["checks"]


def test_submission_quality_gate_live_context_accepts_member_comments(
    monkeypatch,
) -> None:
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        if args[:3] == ["gh", "issue", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps([{"number": 319, "title": "MRWK bounty: gate", "state": "OPEN"}]),
                stderr="",
            )
        if args[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps(
                    {
                        "author": {"login": "someone_else"},
                        "createdAt": "2026-05-20T00:00:00Z",
                        "comments": [
                            {
                                "authorAssociation": "MEMBER",
                                "createdAt": "2026-05-25T12:00:00Z",
                            }
                        ],
                    }
                ),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(submission_quality_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        submission_quality_gate,
        "_load_api_bounties",
        lambda repo, api_host: {319: {"number": 319, "state": "OPEN", "awards_remaining": 1}},
    )

    data = submission_quality_gate._load_live_context(
        "ramimbo/mergework",
        "Summary: work\n\nRefs #319\n\nValidation: pytest passed",
        "https://api.example.test",
        14,
    )
    data["now"] = "2026-05-26T12:00:00Z"

    assert data["bounties"][0]["maintainer_activity_verified"] is True
    assert data["bounties"][0]["last_maintainer_activity_at"] == "2026-05-25T12:00:00Z"

    result = evaluate_submission(data)
    assert result["status"] == "pass"
    assert {
        "name": "maintainer_activity",
        "status": "pass",
        "message": "maintainer activity for bounty #319 was seen 1 days ago",
    } in result["checks"]


def test_submission_quality_gate_warns_when_live_payability_is_unverified(
    monkeypatch, capsys, tmp_path
) -> None:
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        if args[:3] == ["gh", "issue", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps([{"number": 319, "title": "MRWK bounty: gate", "state": "OPEN"}]),
                stderr="",
            )
        if args[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps({"createdAt": "2026-05-20T00:00:00Z", "comments": []}),
                stderr="",
            )
        raise AssertionError(args)

    def fake_load_api_bounties(repo, api_host):
        raise RuntimeError("MergeWork API bounty data unavailable: offline")

    monkeypatch.setattr(submission_quality_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(submission_quality_gate, "_load_api_bounties", fake_load_api_bounties)
    text_path = tmp_path / "draft.md"
    text_path.write_text(
        "Summary: work\n\nRefs #319\n\nValidation: pytest passed",
        encoding="utf-8",
    )

    assert main(["--text-file", str(text_path), "--format", "json"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "warn"
    assert "load_warning" in output
    assert {
        "name": "bounty_payable",
        "status": "warn",
        "message": "referenced bounty #319 payability could not be verified",
    } in output["checks"]
    assert {
        "name": "maintainer_activity",
        "status": "warn",
        "message": "recent maintainer activity for bounty #319 could not be verified",
    } in output["checks"]

    assert main(["--text-file", str(text_path), "--format", "text"]) == 0
    text_output = capsys.readouterr().out
    assert "Warning: MergeWork API bounty data unavailable: offline" in text_output
    assert "referenced bounty #319 payability could not be verified" in text_output


def test_submission_quality_gate_fails_closed_bounty_before_unverified_warning() -> None:
    result = evaluate_submission(
        {
            "submission_text": "Summary: work\n\nRefs #319\n\nValidation: pytest passed",
            "bounties": [{"number": 319, "state": "CLOSED", "payability_verified": False}],
            "pull_requests": [],
        }
    )

    assert result["status"] == "fail"
    assert {
        "name": "bounty_payable",
        "status": "fail",
        "message": "referenced bounty #319 is closed or exhausted",
    } in result["checks"]
    assert {
        "name": "bounty_payable",
        "status": "warn",
        "message": "referenced bounty #319 payability could not be verified",
    } not in result["checks"]


def test_submission_quality_gate_treats_incomplete_api_bounty_as_unverified(
    monkeypatch,
) -> None:
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        if args[:3] == ["gh", "issue", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps([{"number": 319, "title": "MRWK bounty: gate", "state": "OPEN"}]),
                stderr="",
            )
        if args[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps({"createdAt": "2026-05-20T00:00:00Z", "comments": []}),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(submission_quality_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        submission_quality_gate,
        "_load_api_bounties",
        lambda repo, api_host: {319: {"number": 319, "state": "OPEN", "awards_remaining": None}},
    )

    data = submission_quality_gate._load_live_context(
        "ramimbo/mergework",
        "Summary: work\n\nRefs #319\n\nValidation: pytest passed",
        "https://api.example.test",
    )
    result = evaluate_submission(data)

    assert result["status"] == "warn"
    assert {
        "name": "bounty_payable",
        "status": "warn",
        "message": "referenced bounty #319 payability could not be verified",
    } in result["checks"]
