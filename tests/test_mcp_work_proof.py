from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone(timedelta(0))


from app.mcp_work_proof import (
    generic_work_proof_guidance_json,
    work_proof_guidance,
    work_proof_guidance_json,
    work_proof_submission_requirements,
)
from app.models import Bounty


def _action(requirements: dict[str, object], action_id: str) -> dict[str, object]:
    next_actions = requirements["next_actions"]
    assert isinstance(next_actions, list)
    matches = [action for action in next_actions if action["id"] == action_id]
    assert len(matches) == 1
    return matches[0]


def _bounty(**overrides: object) -> Bounty:
    values = {
        "id": 7,
        "repo": "ramimbo/mergework",
        "issue_number": 377,
        "issue_url": "https://github.com/ramimbo/mergework/issues/377",
        "title": "Code health boundary",
        "reward_microunits": 200_000_000,
        "reserved_microunits": 1_200_000_000,
        "max_awards": 6,
        "awards_paid": 0,
        "status": "open",
        "acceptance": "Extract a coherent subsystem with focused tests.",
        "created_at": datetime(2026, 5, 26, tzinfo=UTC),
    }
    values.update(overrides)
    return Bounty(**values)


def test_work_proof_submission_requirements_choose_open_bounty_for_closed_state() -> None:
    requirements = work_proof_submission_requirements(
        bounty_id=7,
        issue_number=377,
        availability="closed",
    )

    assert requirements["reference_formats"] == ["Bounty #377", "Refs #377"]
    assert requirements["submission_mode"] == "pr_or_evidence"
    assert requirements["expected_artifact"] == "focused PR, issue, report, or evidence URL"
    assert requirements["attempt_endpoint"] == "/api/v1/bounties/7/attempts"
    assert _action(requirements, "choose_open_bounty") == {
        "id": "choose_open_bounty",
        "required": True,
        "text": "Do not open or claim new work for this bounty unless a maintainer reopens it.",
    }
    assert "price claims" in requirements["public_metadata_must_avoid"]


def test_work_proof_submission_requirements_marks_issue_mode() -> None:
    requirements = work_proof_submission_requirements(
        bounty_id=96,
        issue_number=649,
        availability="open",
        title="MRWK bounty: accepted proposed-work requests, round 1",
        acceptance=(
            "Accepted work is a new proposed-work issue in ramimbo/mergework. "
            "Payment review uses the proposed-work issue URL as submission_url."
        ),
    )

    assert requirements["submission_mode"] == "issue"
    assert requirements["submission_url_kind"] == "github_issue"
    assert requirements["expected_artifact"] == "new proposed-work GitHub issue URL"
    assert requirements["claim_command"] == "/claim #649"
    assert requirements["attempt_endpoint"] == "/api/v1/bounties/96/attempts"
    assert requirements["attempt_endpoint_applicability"] == ("not_required_for_issue_submission")
    next_action_ids = [action["id"] for action in requirements["next_actions"]]
    assert next_action_ids[:4] == [
        "confirm_award_slot",
        "check_duplicate_scope",
        "open_proposed_work_issue",
        "link_bounty_issue",
    ]


def test_work_proof_submission_requirements_distinguishes_full_bounty_state() -> None:
    requirements = work_proof_submission_requirements(
        bounty_id=7,
        issue_number=377,
        availability="full",
    )

    assert requirements["reference_formats"] == ["Bounty #377", "Refs #377"]
    assert requirements["attempt_endpoint"] == "/api/v1/bounties/7/attempts"
    assert _action(requirements, "watch_for_award_slot") == {
        "id": "watch_for_award_slot",
        "required": True,
        "text": (
            "This bounty is open but has no award slots remaining; check for new "
            "capacity before submitting new work."
        ),
    }
    next_action_ids = {action["id"] for action in requirements["next_actions"]}
    assert "choose_open_bounty" not in next_action_ids
    assert "price claims" in requirements["public_metadata_must_avoid"]


def test_work_proof_guidance_json_reports_open_bounty_state() -> None:
    guidance = work_proof_guidance_json(_bounty())

    assert guidance["bounty_id"] == 7
    assert guidance["availability"] == "open_for_submissions"
    assert guidance["can_submit"] is True
    assert guidance["awards_remaining"] == 6
    assert guidance["max_awards"] == 6
    assert guidance["awards_paid"] == 0
    assert guidance["available_mrwk"] == "1200"
    next_actions = guidance["submission_requirements"]["next_actions"]
    assert any(action["id"] == "confirm_award_slot" for action in next_actions)


def test_work_proof_guidance_json_uses_issue_submission_mode() -> None:
    guidance = work_proof_guidance_json(
        _bounty(
            issue_number=649,
            title="MRWK bounty: accepted proposed-work requests, round 1",
            acceptance=(
                "Accepted work is a new proposed-work issue in ramimbo/mergework. "
                "Payment review uses the proposed-work issue URL as submission_url."
            ),
        )
    )

    requirements = guidance["submission_requirements"]
    assert requirements["submission_mode"] == "issue"
    assert requirements["expected_artifact"] == "new proposed-work GitHub issue URL"


def test_work_proof_guidance_json_reports_open_full_bounty_state() -> None:
    guidance = work_proof_guidance_json(
        _bounty(awards_paid=6, reserved_microunits=0),
    )

    assert guidance["availability"] == "not_currently_open"
    assert guidance["can_submit"] is False
    assert guidance["availability_warnings"] == ["bounty has no award slots remaining"]
    next_actions = guidance["submission_requirements"]["next_actions"]
    assert any(action["id"] == "watch_for_award_slot" for action in next_actions)


def test_work_proof_guidance_returns_reviewable_text() -> None:
    text = work_proof_guidance(_bounty())

    assert "Bounty #377: Code health boundary" in text
    assert "Repository: ramimbo/mergework" in text
    assert "Status: open (open for submissions); awards remaining: 6 of 6" in text
    assert "Reward: 200 MRWK per accepted award" in text
    assert "Do not include private keys" in text


def test_generic_work_proof_guidance_reuses_shared_submission_requirements() -> None:
    guidance = generic_work_proof_guidance_json()

    assert guidance["status"] == "generic_guidance"
    assert guidance["max_awards"] is None
    assert guidance["awards_paid"] is None
    assert guidance["available_mrwk"] is None
    assert guidance["title"] is None
    assert guidance["submission_requirements"]["reference_formats"] == [
        "Bounty #<issue_number>",
        "Refs #<issue_number>",
    ]
    next_actions = guidance["submission_requirements"]["next_actions"]
    assert any(action["id"] == "select_bounty" for action in next_actions)
    assert any("private keys" in rule for rule in guidance["safety_rules"])
