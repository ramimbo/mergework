from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import claim_inventory
from scripts.claim_inventory import analyze_inventory, format_markdown_report, main

ROOT = Path(__file__).resolve().parents[1]


def test_claim_inventory_uses_canonical_api_host_by_default() -> None:
    assert claim_inventory.DEFAULT_API_HOST == "https://api.mrwk.online"


def _fixture() -> dict[str, object]:
    return {
        "bounties": [
            {
                "id": 85,
                "issue_number": 578,
                "status": "open",
                "awards_remaining": 30,
                "pending_payout_proposals": [
                    {
                        "proposal_id": 67,
                        "executes_after": "2026-06-01T11:41:45Z",
                        "to_account": "github:pending-reviewer",
                        "bounty_id": 85,
                        "accepted_by": "ramimbo",
                        "submission_url": (
                            "https://github.com/ramimbo/mergework/pull/620#pullrequestreview-67"
                        ),
                    }
                ],
            },
            {"id": 87, "issue_number": 581, "status": "open", "awards_remaining": 1},
        ],
        "proofs": [
            {
                "source_url": "https://github.com/ramimbo/mergework/pull/452#pullrequestreview-1",
                "proof_url": "/proofs/abc123",
            },
            {
                "source_url": "https://github.com/ramimbo/mergework/pull/581#discussion_r1",
                "proof_url": "/proofs/discussion-r1",
            },
        ],
        "recent": [
            {
                "submission_url": "https://github.com/ramimbo/mergework/issues/578#issuecomment-4",
                "proof_url": "/proofs/recent-paid",
                "bounty_issue_number": 578,
                "bounty_id": 85,
                "ledger_sequence": 42,
            }
        ],
        "issues": [
            {
                "number": 578,
                "title": "MRWK bounty: review open MergeWork PRs with evidence",
                "url": "https://github.com/ramimbo/mergework/issues/578",
                "labels": ["mrwk:bounty"],
                "author": {"login": "ramimbo"},
                "comments": [
                    {
                        "url": "https://github.com/ramimbo/mergework/issues/578#issuecomment-1",
                        "author": {"login": "eliasx45"},
                        "body": (
                            "/claim "
                            "https://github.com/ramimbo/mergework/pull/452#pullrequestreview-1\n"
                            "Reviewed PR #452 with tests."
                        ),
                    },
                    {
                        "url": "https://github.com/ramimbo/mergework/issues/578#issuecomment-2",
                        "author": {"login": "other-reviewer"},
                        "body": (
                            "/claim "
                            "https://github.com/ramimbo/mergework/pull/533#issuecomment-2\n"
                            "Duplicate review claim."
                        ),
                    },
                    {
                        "url": "https://github.com/ramimbo/mergework/issues/578#issuecomment-3",
                        "author": {"login": "smoke-checker"},
                        "body": (
                            "Smoke-check claim: "
                            "https://github.com/ramimbo/mergework/pull/533#issuecomment-2 "
                            "works on the public activity page."
                        ),
                    },
                    {
                        "url": "https://github.com/ramimbo/mergework/issues/578#issuecomment-4",
                        "author": {"login": "recent-winner"},
                        "body": "/claim https://github.com/ramimbo/mergework/pull/700",
                    },
                    {
                        "url": "https://github.com/ramimbo/mergework/issues/578#issuecomment-5",
                        "author": {"login": "pending-reviewer"},
                        "body": (
                            "/claim "
                            "https://github.com/ramimbo/mergework/"
                            "pull/620#pullrequestreview-67\n"
                            "Reviewed current head while payout is pending."
                        ),
                    },
                    {
                        "url": "https://github.com/ramimbo/mergework/issues/578#issuecomment-6",
                        "author": {"login": "pending-reviewer"},
                        "body": (
                            "Accepted review evidence: "
                            "https://github.com/ramimbo/mergework/"
                            "pull/620#pullrequestreview-67"
                        ),
                    },
                ],
            }
        ],
        "pull_requests": [
            {
                "number": 581,
                "title": "Refs #581: Add claim inventory report",
                "url": "https://github.com/ramimbo/mergework/pull/581",
                "author": {"login": "jakerated-r"},
                "body": "Refs #581\n\nAdds scripts/claim_inventory.py.",
                "comments": [
                    {
                        "url": "https://github.com/ramimbo/mergework/pull/581#issuecomment-1",
                        "author": {"login": "reviewer"},
                        "body": "Looks good after docs smoke.",
                    }
                ],
                "reviews": [
                    {
                        "url": "https://github.com/ramimbo/mergework/pull/581#pullrequestreview-9",
                        "author": {"login": "reviewer"},
                        "body": "Reviewed the fixture mode and markdown output.",
                    }
                ],
                "review_comments": [
                    {
                        "url": "https://github.com/ramimbo/mergework/pull/581#discussion_r1",
                        "author": {"login": "inline-reviewer"},
                        "body": "The markdown claim row still looks traceable.",
                    }
                ],
            },
            {
                "number": 582,
                "title": "Refs #581: Add another inventory report",
                "url": "https://github.com/ramimbo/mergework/pull/582",
                "author": {"login": "jakerated-r"},
                "body": "Claiming another read-only report.",
                "comments": [
                    {
                        "author": {"login": "maintainer"},
                        "body": "Looks fine.",
                    }
                ],
            },
            {
                "number": 999,
                "title": "Small cleanup with no bounty link",
                "url": "https://github.com/ramimbo/mergework/pull/999",
                "author": {"login": "unknown"},
                "body": "Claiming this small cleanup, but no bounty reference is included.",
            },
            {
                "number": 1000,
                "title": "Refs #9999: unknown bounty",
                "url": "https://github.com/ramimbo/mergework/pull/1000",
                "author": {"login": "unknown"},
                "body": "Refs #9999\n\nValidation: pytest passed.",
            },
        ],
    }


def test_claim_inventory_classifies_required_statuses(tmp_path, capsys) -> None:
    report = analyze_inventory(_fixture(), api_host="https://api.example.test")

    rows = {row["source_url"]: row for row in report["rows"]}
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-1"]["likely_status"]
        == "already_paid"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-4"]["likely_status"]
        == "already_paid"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-4"]["proof_url"]
        == "https://api.example.test/proofs/recent-paid"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-2"]["likely_status"]
        == "duplicate_candidate"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/pull/999"]["likely_status"]
        == "missing_bounty_ref"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/pull/1000"]["likely_status"] == "unknown_bounty"
    )
    assert rows["https://github.com/ramimbo/mergework/pull/581"]["bounty_id"] == 87
    assert (
        rows["https://github.com/ramimbo/mergework/pull/581#discussion_r1"]["source_type"]
        == "pull_request_review_comment"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/pull/581#discussion_r1"]["proof_url"]
        == "https://api.example.test/proofs/discussion-r1"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/pull/582"]["likely_status"] == "unpaid_candidate"
    )
    assert rows["https://github.com/ramimbo/mergework/pull/582"]["pending_proposal_id"] is None
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-3"]["source_type"]
        == "bounty_issue_comment"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-5"]["likely_status"]
        == "pending_payout"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-6"]["likely_status"]
        == "pending_payout"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-5"][
            "pending_proposal_id"
        ]
        == 67
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-5"][
            "pending_proposal_url"
        ]
        == "https://api.example.test/api/v1/treasury/proposals/67"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-5"][
            "pending_executes_after"
        ]
        == "2026-06-01T11:41:45Z"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-5"]["pending_to_account"]
        == "github:pending-reviewer"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-5"]["pending_bounty_id"]
        == 85
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-5"][
            "pending_accepted_by"
        ]
        == "ramimbo"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-5"][
            "pending_submission_url"
        ]
        == "https://github.com/ramimbo/mergework/pull/620#pullrequestreview-67"
    )
    assert report["summary"]["pending_payout"] == 2
    assert set(report["likely_status_enum"]) >= {
        "already_paid",
        "pending_payout",
        "unpaid_candidate",
        "duplicate_candidate",
        "missing_bounty_ref",
        "unknown_bounty",
        "ignored_or_unclear",
    }

    input_path = tmp_path / "claims.json"
    input_path.write_text(json.dumps(_fixture()), encoding="utf-8")
    assert main(["--input", str(input_path), "--format", "json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["summary"]["already_paid"] == 3


def test_claim_inventory_markdown_report_is_pasteable() -> None:
    markdown = format_markdown_report(analyze_inventory(_fixture()))

    assert "## Claim Inventory" in markdown
    assert "| Status | Bounty | Claimant | Type | Source | Proof/Pending |" in markdown
    assert "`already_paid`" in markdown
    assert "`pending_payout`" in markdown
    assert "https://api.mrwk.online/proofs/abc123" in markdown
    assert "https://api.mrwk.online/api/v1/treasury/proposals/67" in markdown


def test_claim_inventory_paid_proof_overrides_pending_payout() -> None:
    fixture = _fixture()
    proofs = fixture["proofs"]
    assert isinstance(proofs, list)
    proofs.append(
        {
            "source_url": "https://github.com/ramimbo/mergework/pull/620#pullrequestreview-67",
            "proof_url": "/proofs/pending-later-paid",
        }
    )

    report = analyze_inventory(fixture, api_host="https://api.example.test")
    rows = {row["source_url"]: row for row in report["rows"]}

    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-5"]["likely_status"]
        == "already_paid"
    )
    assert (
        rows["https://github.com/ramimbo/mergework/issues/578#issuecomment-5"]["proof_url"]
        == "https://api.example.test/proofs/pending-later-paid"
    )


def test_claim_inventory_live_mode_uses_read_only_calls(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run_gh_json(args: list[str]) -> object:
        calls.append(args)
        if args[:3] == ["gh", "issue", "list"]:
            return [
                {
                    "number": 581,
                    "title": "MRWK bounty: claim inventory",
                    "url": "https://github.com/ramimbo/mergework/issues/581",
                    "labels": [{"name": "mrwk:bounty"}],
                    "author": {"login": "ramimbo"},
                }
            ]
        if args[:3] == ["gh", "issue", "view"]:
            return {
                "number": 581,
                "title": "MRWK bounty: claim inventory",
                "url": "https://github.com/ramimbo/mergework/issues/581",
                "body": "Reward: 500 MRWK",
                "labels": [{"name": "mrwk:bounty"}],
                "author": {"login": "ramimbo"},
                "comments": [],
            }
        if args[:3] == ["gh", "pr", "list"]:
            return [
                {
                    "number": 582,
                    "title": "Refs #581: Add inventory",
                    "url": "https://github.com/ramimbo/mergework/pull/582",
                    "body": "Refs #581",
                    "author": {"login": "bot"},
                    "labels": [],
                }
            ]
        if args[:3] == ["gh", "pr", "view"]:
            return {
                "number": 582,
                "title": "Refs #581: Add inventory",
                "url": "https://github.com/ramimbo/mergework/pull/582",
                "body": "Refs #581",
                "author": {"login": "bot"},
                "labels": [],
                "comments": [],
                "reviews": [],
            }
        if args[:2] == ["gh", "api"]:
            return [
                {
                    "html_url": "https://github.com/ramimbo/mergework/pull/582#discussion_r123",
                    "user": {"login": "reviewer"},
                    "body": "Inline review claim evidence for #581.",
                }
            ]
        raise AssertionError(args)

    monkeypatch.setattr(claim_inventory, "_run_gh_json", fake_run_gh_json)
    monkeypatch.setattr(
        claim_inventory,
        "load_public_api_state",
        lambda api_host: {
            "bounties": [{"id": 87, "issue_number": 581, "status": "open", "awards_remaining": 1}],
            "proofs": [],
            "recent": [],
        },
    )

    data = claim_inventory.load_live_inventory("ramimbo/mergework", "https://api.example.test")

    assert data["bounties"][0]["id"] == 87
    assert data["pull_requests"][0]["review_comments"] == [
        {
            "url": "https://github.com/ramimbo/mergework/pull/582#discussion_r123",
            "author": {"login": "reviewer"},
            "body": "Inline review claim evidence for #581.",
        }
    ]
    allowed_prefixes = {
        ("gh", "issue", "list"),
        ("gh", "issue", "view"),
        ("gh", "pr", "list"),
        ("gh", "pr", "view"),
        ("gh", "api"),
    }
    assert calls, "expected at least one gh invocation"
    assert all(
        tuple(call[:3]) in allowed_prefixes or tuple(call[:2]) in allowed_prefixes for call in calls
    ), calls


def test_claim_inventory_script_entrypoint_loads_shared_parser() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/claim_inventory.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout


def test_claim_inventory_rejects_invalid_api_host(capsys) -> None:
    for bad in ("", "   ", "/relative", "ftp://api.example.test"):
        with pytest.raises(SystemExit) as excinfo:
            main(["--repo", "ramimbo/mergework", "--api-host", bad])
        assert excinfo.value.code == 2
        assert "api host must" in capsys.readouterr().err
