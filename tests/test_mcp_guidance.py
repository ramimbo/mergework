from __future__ import annotations

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis
from app.mcp import (
    generic_work_proof_guidance_json,
    work_proof_guidance,
    work_proof_guidance_json,
)


def test_work_proof_guidance_helpers_shape_bounty_messages(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Code health",
            reward_mrwk="200",
            max_awards=4,
            acceptance="Reduce app.main complexity with focused tests.",
        )

        text = work_proof_guidance(bounty)
        structured = work_proof_guidance_json(bounty)

    assert "Bounty #320: Code health" in text
    assert "Status: open (open for submissions); awards remaining: 4 of 4" in text
    assert "Acceptance: Reduce app.main complexity with focused tests." in text

    assert structured["issue_number"] == 320
    assert structured["availability"] == "open_for_submissions"
    assert structured["can_submit"] is True
    assert structured["awards_remaining"] == 4
    assert structured["reward_mrwk"] == "200"
    assert structured["repository"] == "ramimbo/mergework"
    assert "/claim" in structured["submission_format"]


def test_generic_work_proof_guidance_json_keeps_agent_submission_rules() -> None:
    structured = generic_work_proof_guidance_json()

    assert structured["status"] == "generic_guidance"
    assert structured["availability"] == "unknown_without_bounty"
    assert structured["can_submit"] is None
    assert "mrwk:accepted" in structured["submission_format"]
    assert "private keys" in structured["safety_rules"][0]
