from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import submission_quality_gate
from scripts.submission_quality_gate import evaluate_submission, main

ROOT = Path(__file__).resolve().parents[1]


def test_submission_quality_gate_uses_canonical_api_host_by_default() -> None:
    assert submission_quality_gate.DEFAULT_API_HOST == "https://api.mrwk.online"


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
        "github_linked_issue": "pass",
        "bounty_payable": "pass",
        "summary_present": "pass",
        "evidence_present": "pass",
        "similar_open_pr": "pass",
    }

    input_path = tmp_path / "submission.json"
    input_path.write_text(json.dumps(fixture), encoding="utf-8")
    assert main(["--input", str(input_path), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


def test_submission_quality_gate_script_entrypoint_loads_shared_parser() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/submission_quality_gate.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout


def test_submission_quality_gate_accepts_claim_command_reference() -> None:
    result = evaluate_submission(
        {
            "submission_text": """
            Summary:
            Harden the bounty submission checks.

            /claim #319

            Validation:
            - pytest passed.
            """,
            "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [],
        }
    )

    assert result["status"] == "warn"
    assert result["bounty_reference"] == 319
    assert {
        "name": "bounty_reference",
        "status": "pass",
        "message": "found bounty reference #319",
    } in result["checks"]
    assert {
        "name": "github_linked_issue",
        "status": "warn",
        "message": (
            "MergeWork bounty reference #319 is valid, but GitHub or bot linked-issue "
            "checks may stay skipped without `Refs #319`; use closing keywords only "
            "when the bounty should close"
        ),
    } in result["checks"]


def test_submission_quality_gate_ignores_oversized_numeric_bounty_refs() -> None:
    oversized_ref = "9" * 5000

    result = evaluate_submission(
        {
            "submission_text": (
                f"Summary: add validation\n\nRefs #{oversized_ref}\nRefs #319\n\n"
                "Validation: pytest passed"
            ),
            "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [],
        }
    )

    assert result["status"] == "pass"
    assert result["bounty_reference"] == 319
    assert {
        "name": "bounty_reference",
        "status": "pass",
        "message": "found bounty reference #319",
    } in result["checks"]


def test_submission_quality_gate_warns_for_mergework_only_bounty_keywords() -> None:
    references = (
        "Bounty #319",
        "Claim #319",
        "Claims #319",
    )
    for reference in references:
        result = evaluate_submission(
            {
                "submission_text": f"""
                Summary:
                Harden the bounty reference parser.

                {reference}

                Validation:
                - pytest passed.
                """,
                "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
                "pull_requests": [],
            }
        )

        assert result["status"] == "warn", reference
        assert result["bounty_reference"] == 319
        assert {
            "name": "github_linked_issue",
            "status": "warn",
            "message": (
                "MergeWork bounty reference #319 is valid, but GitHub or bot linked-issue "
                "checks may stay skipped without `Refs #319`; use closing keywords only "
                "when the bounty should close"
            ),
        } in result["checks"]


def test_submission_quality_gate_accepts_github_linking_keywords() -> None:
    references = (
        "Ref #319",
        "Refs #319",
        "Reference #319",
        "References #319",
        "Fix #319",
        "Fixes #319",
        "Fixed #319",
        "Close #319",
        "Closes #319",
        "Closed #319",
        "Resolve #319",
        "Resolves #319",
        "Resolved #319",
    )
    for reference in references:
        result = evaluate_submission(
            {
                "submission_text": f"""
                Summary:
                Harden the bounty reference parser.

                {reference}

                Validation:
                - pytest passed.
                """,
                "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
                "pull_requests": [],
            }
        )

        assert result["status"] == "pass", reference
        assert result["bounty_reference"] == 319


@pytest.mark.parametrize("reference", ("Fixes #319abc", "Fixes #319_abc", "Fixes #319-abc"))
def test_submission_quality_gate_rejects_linking_keyword_issue_suffix(reference: str) -> None:
    result = evaluate_submission(
        {
            "submission_text": (
                f"Summary: add validation\n\n{reference}\n\nValidation: pytest passed"
            ),
            "bounties": [{"number": 319, "state": "OPEN", "awards_remaining": 1}],
            "pull_requests": [],
        }
    )

    assert result["status"] == "fail"
    assert result["bounty_reference"] is None


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
        "message": (
            "submission text must include a bounty reference such as "
            "Bounty #<issue>, Refs #<issue>, Fixes #<issue>, or /claim #<issue>"
        ),
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


def test_submission_quality_gate_passes_when_no_active_attempts() -> None:
    result = evaluate_submission(
        {
            "submission_text": "Summary: add validation\n\nRefs #319\n\nValidation: pytest passed",
            "bounties": [
                {
                    "number": 319,
                    "state": "OPEN",
                    "awards_remaining": 1,
                    "active_attempts": [],
                    "active_attempts_verified": True,
                }
            ],
            "pull_requests": [],
        }
    )

    assert result["status"] == "pass"
    assert result["active_attempts"] == []
    assert {
        "name": "active_attempts",
        "status": "pass",
        "message": "no active attempts found for bounty #319",
    } in result["checks"]


def test_submission_quality_gate_warns_for_one_active_attempt() -> None:
    result = evaluate_submission(
        {
            "submission_text": "Summary: add validation\n\nRefs #319\n\nValidation: pytest passed",
            "bounties": [
                {
                    "number": 319,
                    "state": "OPEN",
                    "awards_remaining": 1,
                    "active_attempts": [
                        {
                            "submitter": "github:agent-one",
                            "source_url": "https://github.com/ramimbo/mergework/pull/12",
                            "status": "active",
                            "expires_at": "2026-05-27T00:00:00Z",
                        }
                    ],
                    "active_attempts_verified": True,
                }
            ],
            "pull_requests": [],
        }
    )

    assert result["status"] == "warn"
    assert result["active_attempts"] == [
        {
            "submitter": "github:agent-one",
            "source_url": "https://github.com/ramimbo/mergework/pull/12",
            "status": "active",
            "expires_at": "2026-05-27T00:00:00Z",
        }
    ]
    assert {
        "name": "active_attempts",
        "status": "warn",
        "message": "1 active attempt(s) already exist for bounty #319",
    } in result["checks"]


def test_submission_quality_gate_warns_for_multiple_active_attempts() -> None:
    result = evaluate_submission(
        {
            "submission_text": "Summary: add validation\n\nRefs #319\n\nValidation: pytest passed",
            "bounties": [
                {
                    "number": 319,
                    "state": "OPEN",
                    "awards_remaining": 1,
                    "active_attempts": [
                        {"submitter": "github:agent-one", "status": "active"},
                        {"submitter": "github:agent-two", "status": "active"},
                    ],
                    "active_attempts_verified": True,
                }
            ],
            "pull_requests": [],
        }
    )

    assert result["status"] == "warn"
    assert {
        "name": "active_attempts",
        "status": "warn",
        "message": "2 active attempt(s) already exist for bounty #319",
    } in result["checks"]


def test_submission_quality_gate_warns_when_active_attempts_unavailable() -> None:
    result = evaluate_submission(
        {
            "submission_text": "Summary: add validation\n\nRefs #319\n\nValidation: pytest passed",
            "bounties": [
                {
                    "number": 319,
                    "state": "OPEN",
                    "awards_remaining": 1,
                    "active_attempts": [],
                    "active_attempts_verified": False,
                }
            ],
            "pull_requests": [],
        }
    )

    assert result["status"] == "warn"
    assert {
        "name": "active_attempts",
        "status": "warn",
        "message": "active attempts for bounty #319 could not be verified",
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


def test_submission_quality_gate_matches_similar_pr_when_title_starts_with_ref() -> None:
    result = evaluate_submission(
        {
            "submission_text": """
            Refs #319: Add agent submission quality gate.

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
    assert {
        "name": "similar_open_pr",
        "status": "warn",
        "message": "similar open PRs already reference this bounty",
    } in result["checks"]
    assert result["similar_open_prs"] == [
        {
            "number": 12,
            "title": "Add agent submission quality gate",
            "url": "https://github.com/ramimbo/mergework/pull/12",
        }
    ]


def test_submission_quality_gate_warns_for_multiple_bounty_references() -> None:
    result = evaluate_submission(
        {
            "submission_text": """
            Summary:
            Add focused quality-gate validation.

            Bounty #320
            Closes #319

            Validation:
            - pytest passed.
            """,
            "bounties": [
                {"number": 319, "state": "CLOSED", "awards_remaining": 0},
                {"number": 320, "state": "OPEN", "awards_remaining": 1},
            ],
            "pull_requests": [],
        }
    )

    assert result["status"] == "warn"
    assert result["bounty_reference"] == 320
    assert {
        "name": "bounty_payable",
        "status": "pass",
        "message": "referenced bounty #320 is open",
    } in result["checks"]
    assert {
        "name": "single_bounty_reference",
        "status": "warn",
        "message": (
            "submission references multiple bounties (#320, #319); "
            "keep one bounty target or split the work"
        ),
    } in result["checks"]
    assert {
        "name": "bounty_payable",
        "status": "fail",
        "message": "referenced bounty #319 is closed or exhausted",
    } not in result["checks"]


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


def test_submission_quality_gate_warns_for_malformed_maintainer_age_threshold() -> None:
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
                    "max_maintainer_age_days": "not-a-number",
                }
            ],
            "pull_requests": [],
        }
    )

    assert result["status"] == "warn"
    assert {
        "name": "maintainer_activity",
        "status": "warn",
        "message": "recent maintainer activity for bounty #319 could not be verified",
    } in result["checks"]


def test_submission_quality_gate_warns_for_none_maintainer_age_threshold() -> None:
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
                    "max_maintainer_age_days": None,
                }
            ],
            "pull_requests": [],
        }
    )

    assert result["status"] == "warn"
    assert {
        "name": "maintainer_activity",
        "status": "warn",
        "message": "recent maintainer activity for bounty #319 could not be verified",
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


def test_submission_quality_gate_warns_when_live_collections_hit_safety_caps(
    monkeypatch,
) -> None:
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "number": number,
                            "title": "Open PR",
                            "body": "Refs #319",
                            "state": "OPEN",
                            "url": f"https://github.com/ramimbo/mergework/pull/{number}",
                        }
                        for number in range(1, submission_quality_gate.GH_PR_SAFETY_CAP + 1)
                    ]
                ),
                stderr="",
            )
        if args[:3] == ["gh", "issue", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "number": number,
                            "title": f"MRWK bounty: cap test {number}",
                            "state": "OPEN",
                        }
                        for number in range(
                            319,
                            319 + submission_quality_gate.GH_ISSUE_SAFETY_CAP,
                        )
                    ]
                ),
                stderr="",
            )
        if args[:3] == ["gh", "issue", "view"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps(
                    {
                        "author": {"login": "ramimbo"},
                        "createdAt": "2026-05-25T12:00:00Z",
                        "comments": [],
                    }
                ),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(submission_quality_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        submission_quality_gate,
        "_load_api_bounties",
        lambda repo, api_host: {
            319: {"id": 66, "number": 319, "state": "open", "awards_remaining": 1}
        },
    )
    monkeypatch.setattr(
        submission_quality_gate, "_load_api_attempts", lambda api_host, bounty_id: []
    )

    data = submission_quality_gate._load_live_context(
        "ramimbo/mergework",
        "Summary: work\n\nRefs #319\n\nValidation: pytest passed",
        "https://api.example.test",
    )
    result = evaluate_submission(data)

    assert result["status"] == "warn"
    assert (
        f"gh pr list reached the {submission_quality_gate.GH_PR_SAFETY_CAP}" in data["load_warning"]
    )
    assert (
        f"gh issue list reached the {submission_quality_gate.GH_ISSUE_SAFETY_CAP}"
        in data["load_warning"]
    )
    assert {
        "name": "source_completeness",
        "status": "warn",
        "message": data["load_warning"],
    } in result["checks"]


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


def test_submission_quality_gate_live_context_warns_when_attempt_id_missing(
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
        lambda repo, api_host: {319: {"number": 319, "state": "OPEN", "awards_remaining": 1}},
    )

    data = submission_quality_gate._load_live_context(
        "ramimbo/mergework",
        "Summary: work\n\nRefs #319\n\nValidation: pytest passed",
        "https://api.example.test",
    )
    result = evaluate_submission(data)

    assert data["bounties"][0]["active_attempts"] == []
    assert data["bounties"][0]["active_attempts_verified"] is False
    assert (
        "active attempts unavailable for bounty #319: "
        "MergeWork API bounty id unavailable for attempts lookup"
    ) in data["load_warning"]
    assert result["status"] == "warn"
    assert {
        "name": "active_attempts",
        "status": "warn",
        "message": "active attempts for bounty #319 could not be verified",
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
        lambda repo, api_host: {
            319: {"id": 11, "number": 319, "state": "OPEN", "awards_remaining": 1}
        },
    )
    monkeypatch.setattr(
        submission_quality_gate, "_load_api_attempts", lambda api_host, bounty_id: []
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
        lambda repo, api_host: {
            319: {"id": 11, "number": 319, "state": "OPEN", "awards_remaining": 1}
        },
    )
    monkeypatch.setattr(
        submission_quality_gate, "_load_api_attempts", lambda api_host, bounty_id: []
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
