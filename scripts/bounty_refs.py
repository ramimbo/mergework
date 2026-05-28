from __future__ import annotations

import re

LINKED_BOUNTY_VERBS = r"bounty|claims?|close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|references?"
ISSUE_NUMBER_BOUNDARY = r"(?![A-Za-z0-9_-])"
BOUNTY_REF_RE = re.compile(
    rf"\b(?:{LINKED_BOUNTY_VERBS})\s*:?\s+`?#(\d+)`?{ISSUE_NUMBER_BOUNDARY}",
    re.IGNORECASE,
)
GITHUB_ISSUE_URL_RE = re.compile(
    rf"https://github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/"
    rf"(?P<number>\d+){ISSUE_NUMBER_BOUNDARY}",
    re.IGNORECASE,
)
LEADING_BOUNTY_REF_RE = re.compile(
    rf"^/?(?:{LINKED_BOUNTY_VERBS})\s*:?\s+`?#\d+`?\s*[:-]?\s*",
    re.IGNORECASE,
)
