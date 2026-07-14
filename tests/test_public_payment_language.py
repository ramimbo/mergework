from __future__ import annotations

from scripts.public_payment_language import (
    SUGGESTED_REPLACEMENT,
    find_payment_language_violations,
    format_violation_report,
)


def test_find_payment_language_flags_payout_boundary_heading() -> None:
    violations = find_payment_language_violations(
        "## Payout boundary\nThis submission awaits review."
    )
    assert any("Payout boundary" in item for item in violations)


def test_find_payment_language_flags_legacy_withdrawable_phrasing() -> None:
    violations = find_payment_language_violations(
        "This reward is not confirmed or withdrawable yet."
    )
    assert any("withdrawable" in item for item in violations)


def test_find_payment_language_flags_reserved_status_assertion() -> None:
    violations = find_payment_language_violations("This submission is paid.")
    assert any("reserved payment/status wording" in item for item in violations)


def test_find_payment_language_allows_neutral_submission_status() -> None:
    violations = find_payment_language_violations(
        "## Submission status\nAcceptance and proof are tracked separately."
    )
    assert violations == []


def test_find_payment_language_allowlists_instructional_lines() -> None:
    violations = find_payment_language_violations(
        'Do not write "not confirmed or withdrawable" in public drafts.\n'
        "Reserve words such as paid/settled for ledger proofs only."
    )
    assert violations == []


def test_format_violation_report_includes_suggestion() -> None:
    report = format_violation_report(["example violation"])
    assert "example violation" in report
    assert SUGGESTED_REPLACEMENT in report
