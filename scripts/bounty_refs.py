from __future__ import annotations

import re

GITHUB_CLOSING_ISSUE_VERBS = r"close[sd]?|fix(?:e[sd])?|resolve[sd]?"
LINKED_BOUNTY_VERBS = rf"bounty|claims?|{GITHUB_CLOSING_ISSUE_VERBS}|refs?|references?"
GITHUB_LINKED_ISSUE_VERBS = rf"{GITHUB_CLOSING_ISSUE_VERBS}|refs?|references?"
BOUNTY_REF_RE = re.compile(
    rf"\b(?:{LINKED_BOUNTY_VERBS})\s*:?\s+`?#(\d+)`?(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
GITHUB_LINKED_ISSUE_RE = re.compile(
    rf"\b(?:{GITHUB_LINKED_ISSUE_VERBS})\s*:?\s+`?#(\d+)`?(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
GITHUB_CLOSING_ISSUE_RE = re.compile(
    rf"\b(?P<verb>{GITHUB_CLOSING_ISSUE_VERBS})\s*:?\s+`?#(?P<issue>\d+)`?"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
LEADING_BOUNTY_REF_RE = re.compile(
    rf"^/?(?:{LINKED_BOUNTY_VERBS})\s*:?\s+`?#\d+`?\s*[:-]?\s*",
    re.IGNORECASE,
)
