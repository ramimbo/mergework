from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models import Bounty
from app.serializers import bounty_to_dict

SubmissionAvailability = Literal["open", "full", "closed", "unknown"]


def work_proof_guidance(bounty: Bounty, session: Session | None = None) -> str:
    bounty_data = bounty_to_dict(bounty, session=session)
    remaining_awards = int(
        bounty_data.get("effective_awards_remaining", bounty_data["awards_remaining"])
    )
    availability = (
        "open for submissions"
        if bounty_data["status"] == "open" and remaining_awards > 0
        else "not currently open for new submissions"
    )
    return "\n".join(
        [
            f"Bounty #{bounty_data['issue_number']}: {bounty_data['title']}",
            f"Internal bounty id: {bounty_data['id']}",
            f"Repository: {bounty_data['repo']}",
            f"Issue: {bounty_data['issue_url']}",
            (
                f"Status: {bounty_data['status']} ({availability}); "
                f"awards remaining: {bounty_data['awards_remaining']} "
                f"of {bounty_data['max_awards']}; effectively remaining: {remaining_awards}"
            ),
            f"Availability note: {bounty_data['availability_note']}",
            f"Reward: {bounty_data['reward_mrwk']} MRWK per accepted award",
            f"Acceptance: {bounty_data['acceptance']}",
            (
                "Submit: open a focused PR or issue that links this bounty, include "
                "specific test or behavior evidence, then comment /claim with the PR "
                "or evidence URL and verification summary."
            ),
            (
                "Do not include private keys, seed material, secrets, deployment "
                "credentials, private vulnerability details, or price claims."
            ),
        ]
    )


def work_proof_submission_requirements(
    *,
    bounty_id: int | None,
    issue_number: int | None,
    availability: SubmissionAvailability,
) -> dict[str, Any]:
    issue_ref = str(issue_number) if issue_number is not None else "<issue_number>"
    bounty_ref = str(bounty_id) if bounty_id is not None else "<bounty_id>"
    if availability == "open":
        first_action = {
            "id": "confirm_award_slot",
            "required": True,
            "text": "Confirm this bounty is open and has at least one award slot remaining.",
        }
    elif availability == "full":
        first_action = {
            "id": "watch_for_award_slot",
            "required": True,
            "text": (
                "This bounty is open but has no award slots remaining; check for new "
                "capacity before submitting new work."
            ),
        }
    elif availability == "closed":
        first_action = {
            "id": "choose_open_bounty",
            "required": True,
            "text": "Do not open or claim new work for this bounty unless a maintainer reopens it.",
        }
    else:
        first_action = {
            "id": "select_bounty",
            "required": True,
            "text": "Select a concrete open bounty before submitting work proof.",
        }
    return {
        "reference_formats": [f"Bounty #{issue_ref}", f"Refs #{issue_ref}"],
        "claim_command": "/claim",
        "attempt_endpoint": f"/api/v1/bounties/{bounty_ref}/attempts",
        "evidence_required": [
            "focused PR, issue, report, or evidence URL",
            "short verification summary",
            "tests, command output, screenshots, or reproduction steps when relevant",
        ],
        "acceptance_trigger": "maintainer_mrwk_accepted_label_or_admin_payout",
        "public_metadata_must_avoid": [
            "private keys",
            "seed material",
            "secrets",
            "deployment credentials",
            "private vulnerability details",
            "price claims",
        ],
        "next_actions": [
            first_action,
            {
                "id": "check_duplicate_scope",
                "required": True,
                "text": "Confirm no active claim or duplicate PR already covers the same scope.",
            },
            {
                "id": "keep_scope_focused",
                "required": True,
                "text": "Keep changes directly tied to one bounty issue.",
            },
            {
                "id": "include_bounty_reference",
                "required": True,
                "text": f"Include Bounty #{issue_ref} or Refs #{issue_ref} in the submission.",
            },
            {
                "id": "include_review_evidence",
                "required": True,
                "text": "Include reviewable validation evidence before claiming.",
            },
            {
                "id": "wait_for_maintainer_acceptance",
                "required": True,
                "text": (
                    "Payment requires mrwk:accepted or an admin payout; merge or CI "
                    "alone is not acceptance."
                ),
            },
        ],
    }


def _submission_availability(bounty_data: dict[str, Any]) -> SubmissionAvailability:
    awards_remaining = int(
        bounty_data.get("effective_awards_remaining", bounty_data["awards_remaining"])
    )
    if bounty_data["status"] == "open":
        return "open" if awards_remaining > 0 else "full"
    return "closed"


def work_proof_guidance_json(bounty: Bounty, session: Session | None = None) -> dict[str, Any]:
    bounty_data = bounty_to_dict(bounty, session=session)
    submission_availability = _submission_availability(bounty_data)
    can_submit = submission_availability == "open"
    availability_warnings = []
    if bounty_data["status"] != "open":
        availability_warnings.append(f"bounty is {bounty_data['status']}")
    if bounty_data["effective_awards_remaining"] <= 0:
        availability_warnings.append("bounty has no award slots remaining")
    if bounty_data["availability_state"] == "pending_payouts_partial":
        availability_warnings.append(
            "capacity reduced by pending payout proposals; use "
            f"effective_awards_remaining before submitting ({bounty_data['availability_note']})"
        )
    elif bounty_data["availability_state"] not in {"open", "full", bounty_data["status"]}:
        availability_warnings.append(bounty_data["availability_note"])
    return {
        "bounty_id": bounty_data["id"],
        "issue_number": bounty_data["issue_number"],
        "status": bounty_data["status"],
        "availability": "open_for_submissions" if can_submit else "not_currently_open",
        "can_submit": can_submit,
        "availability_warnings": availability_warnings,
        "awards_remaining": bounty_data["awards_remaining"],
        "effective_awards_remaining": bounty_data["effective_awards_remaining"],
        "max_awards": bounty_data["max_awards"],
        "awards_paid": bounty_data["awards_paid"],
        "reward_mrwk": bounty_data["reward_mrwk"],
        "available_mrwk": bounty_data["available_mrwk"],
        "effective_available_mrwk": bounty_data["effective_available_mrwk"],
        "availability_state": bounty_data["availability_state"],
        "availability_note": bounty_data["availability_note"],
        "repository": bounty_data["repo"],
        "issue_url": bounty_data["issue_url"],
        "title": bounty_data["title"],
        "acceptance": bounty_data["acceptance"],
        "submission_format": (
            "Open a focused PR or issue that links this bounty, include specific "
            "test or behavior evidence, then comment /claim with the PR or "
            "evidence URL and verification summary."
        ),
        "submission_requirements": work_proof_submission_requirements(
            bounty_id=bounty_data["id"],
            issue_number=bounty_data["issue_number"],
            availability=submission_availability,
        ),
        "safety_rules": [
            "Do not include private keys, seed material, secrets, deployment "
            "credentials, private vulnerability details, or price claims."
        ],
    }


def generic_work_proof_guidance_json() -> dict[str, Any]:
    return {
        "bounty_id": None,
        "issue_number": None,
        "status": "generic_guidance",
        "availability": "unknown_without_bounty",
        "can_submit": None,
        "availability_warnings": [],
        "awards_remaining": None,
        "max_awards": None,
        "awards_paid": None,
        "reward_mrwk": None,
        "available_mrwk": None,
        "repository": None,
        "issue_url": None,
        "title": None,
        "acceptance": None,
        "submission_format": (
            "Open a focused PR or issue, reference the MRWK bounty, include test "
            "evidence, and wait for a maintainer to apply mrwk:accepted."
        ),
        "submission_requirements": work_proof_submission_requirements(
            bounty_id=None,
            issue_number=None,
            availability="unknown",
        ),
        "safety_rules": [
            "Do not include private keys, seed material, secrets, deployment "
            "credentials, private vulnerability details, or price claims."
        ],
    }
