from __future__ import annotations

import re

LINKED_BOUNTY_VERBS = r"bounty|claims?|close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|references?"
BOUNTY_REF_RE = re.compile(
    rf"\b(?:{LINKED_BOUNTY_VERBS})\s*:?\s+`?#(\d+)`?(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
LEADING_BOUNTY_REF_RE = re.compile(
    rf"^/?(?:{LINKED_BOUNTY_VERBS})\s*:?\s+`?#\d+`?\s*[:-]?\s*",
    re.IGNORECASE,
)
