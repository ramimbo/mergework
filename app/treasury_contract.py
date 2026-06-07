from __future__ import annotations

TREASURY_ACTIONS = frozenset({"create_bounty", "pay_bounty", "close_bounty"})
SUBJECTIVE_CHALLENGE = "subjective_note"
MACHINE_CHALLENGES = frozenset(
    {
        "duplicate_bounty",
        "bounty_not_open",
        "submission_already_paid",
        "insufficient_reserve",
        "epoch_cap_exceeded",
    }
)
CHALLENGE_TYPES = MACHINE_CHALLENGES | {SUBJECTIVE_CHALLENGE}
