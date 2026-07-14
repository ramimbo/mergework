"""Detect premature payment/status wording in public submission text."""

from __future__ import annotations

import re

SUGGESTED_REPLACEMENT = (
    "Use a neutral 'Submission status' section and note that acceptance and any "
    "later proof or ledger outcome are tracked by maintainers through the bounty "
    "issue and public rows."
)

_PAYOUT_BOUNDARY_RE = re.compile(r"payout\s+boundary", re.IGNORECASE)
_LEGACY_WITHDRAWABLE_RE = re.compile(
    r"not\s+(?:confirmed|earned)\s+or\s+withdrawable",
    re.IGNORECASE,
)
_HEADING_PREFIX_RE = re.compile(r"^#{1,6}\s+")

_ALLOWLIST_LINE_RES = (
    re.compile(r"no payout execution", re.IGNORECASE),
    re.compile(r"payment lifecycle", re.IGNORECASE),
    re.compile(r"pay_bounty proposal", re.IGNORECASE),
    re.compile(r"proof-backed", re.IGNORECASE),
    re.compile(r"does not (?:create|execute|trigger|mutate)", re.IGNORECASE),
    re.compile(r"pending payout", re.IGNORECASE),
    re.compile(r"accepted for payout review", re.IGNORECASE),
    re.compile(r"reserve(?:s|d)? words", re.IGNORECASE),
    re.compile(r"do not (?:write|describe|claim)", re.IGNORECASE),
)

_RESERVED_STATUS_ASSERTION_RES = (
    re.compile(
        r"\b(?:is|was|are|were|already|marked as|considered)\s+"
        r"(?:paid|settled|received|withdrawable)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:paid|settled|received|withdrawable)\s+(?:claim|status|reward|payout)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:claim|submission|work)\s+(?:is|was)\s+(?:paid|settled|received|withdrawable)\b",
        re.IGNORECASE,
    ),
)


def _line_is_allowlisted(line: str) -> bool:
    return any(pattern.search(line) for pattern in _ALLOWLIST_LINE_RES)


def _content_line(line: str) -> str:
    """Strip markdown heading markers so heading wording is still checked."""
    stripped = line.strip()
    if not stripped:
        return ""
    return _HEADING_PREFIX_RE.sub("", stripped).strip()


def find_payment_language_violations(text: str) -> list[str]:
    """Return human-readable violations for premature payment/status wording."""
    if not text or not text.strip():
        return []

    violations: list[str] = []
    for line in text.splitlines():
        content = _content_line(line)
        if not content:
            continue
        if _line_is_allowlisted(line) or _line_is_allowlisted(content):
            continue
        if _PAYOUT_BOUNDARY_RE.search(content):
            violations.append(
                "deprecated 'Payout boundary' heading found; prefer neutral "
                "'Submission status' wording"
            )
            break
        if _LEGACY_WITHDRAWABLE_RE.search(content):
            violations.append(
                "legacy 'not confirmed or withdrawable' phrasing found; "
                "use neutral submission status language"
            )
            break

    for line in text.splitlines():
        content = _content_line(line)
        if not content:
            continue
        if _line_is_allowlisted(line) or _line_is_allowlisted(content):
            continue
        for pattern in _RESERVED_STATUS_ASSERTION_RES:
            if pattern.search(content):
                violations.append(
                    f"reserved payment/status wording used as a claim assertion: {content[:120]}"
                )
                break

    seen: set[str] = set()
    unique: list[str] = []
    for item in violations:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def format_violation_report(violations: list[str]) -> str:
    if not violations:
        return "No premature payment/status wording found."
    lines = ["Premature payment/status wording:"]
    lines.extend(f"- {item}" for item in violations)
    lines.append(f"Suggestion: {SUGGESTED_REPLACEMENT}")
    return "\n".join(lines)
