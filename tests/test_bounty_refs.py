from __future__ import annotations

from scripts.bounty_refs import (
    BOUNTY_REF_RE,
    GITHUB_CLOSING_ISSUE_RE,
    GITHUB_LINKED_ISSUE_RE,
    LEADING_BOUNTY_REF_RE,
)


def test_bounty_ref_regex_accepts_claim_bounty_and_reference_forms() -> None:
    text = "Bounty #936, /claim #935, refs: `#944`, and closes #1010"

    assert BOUNTY_REF_RE.findall(text) == ["936", "935", "944", "1010"]


def test_bounty_ref_regex_ignores_bare_or_embedded_issue_numbers() -> None:
    text = "Bare #936 is just discussion, bounty #12abc is not a valid ref, claim #34_ok"

    assert BOUNTY_REF_RE.findall(text) == []


def test_linked_issue_regex_excludes_bounty_only_verbs() -> None:
    text = "Bounty #936, claim #935, references #944, fixes: `#1010`"

    assert GITHUB_LINKED_ISSUE_RE.findall(text) == ["944", "1010"]


def test_closing_issue_regex_reports_verb_and_issue_number() -> None:
    match = GITHUB_CLOSING_ISSUE_RE.search("This PR resolves: `#936` after review.")

    assert match is not None
    assert match.group("verb") == "resolves"
    assert match.group("issue") == "936"


def test_leading_bounty_ref_regex_strips_submission_prefix_only() -> None:
    assert LEADING_BOUNTY_REF_RE.sub("", "/claim #936: tighten parser tests") == (
        "tighten parser tests"
    )
    assert LEADING_BOUNTY_REF_RE.sub("", "Notes before Bounty #936") == ("Notes before Bounty #936")
