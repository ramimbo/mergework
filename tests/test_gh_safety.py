from __future__ import annotations

from scripts import (
    check_live_bounty_closing_refs,
    pr_queue_health,
    review_bounty_candidates,
    submission_quality_gate,
)
from scripts.gh_safety import (
    GH_ISSUE_SAFETY_CAP,
    GH_PR_SAFETY_CAP,
    safety_cap_message,
)


def test_safety_caps_are_unified() -> None:
    assert GH_PR_SAFETY_CAP == 201
    assert GH_ISSUE_SAFETY_CAP == 201


def test_safety_cap_message_composes_prefix_and_hint() -> None:
    message = safety_cap_message("pr", GH_PR_SAFETY_CAP, "use an API-paginated collector")
    assert message == "gh pr list reached the 201 item safety cap; use an API-paginated collector"


def test_safety_cap_message_supports_issue_kind() -> None:
    message = safety_cap_message("issue", GH_ISSUE_SAFETY_CAP, "bounty discovery may be incomplete")
    assert message.startswith("gh issue list reached the 201 item safety cap; ")
    assert message.endswith("bounty discovery may be incomplete")


def test_live_collection_scripts_share_the_policy() -> None:
    assert pr_queue_health.GH_PR_SAFETY_CAP == GH_PR_SAFETY_CAP
    assert pr_queue_health.GH_ISSUE_SAFETY_CAP == GH_ISSUE_SAFETY_CAP
    assert check_live_bounty_closing_refs.GH_PR_SAFETY_CAP == GH_PR_SAFETY_CAP
    assert review_bounty_candidates.GH_PR_SAFETY_CAP == GH_PR_SAFETY_CAP
    assert submission_quality_gate.GH_PR_SAFETY_CAP == GH_PR_SAFETY_CAP
    assert submission_quality_gate.GH_ISSUE_SAFETY_CAP == GH_ISSUE_SAFETY_CAP
