"""Shared safety-cap policy for live GitHub collection scripts.

Several read-only maintenance scripts list live ``gh`` pull requests and issues
with a safety cap so a truncated ``gh`` result is never silently trusted as a
complete live report. Historically each script defined its own cap value and
its own saturation-message text, and those drifted apart: pull-request caps of
200, 201, and 101, and issue caps of 201. This module is the single source of
truth for the cap values and the shared saturation-message prefix, so every
live report fails fast on the same boundary and describes it the same way.
"""

from __future__ import annotations

# Canonical safety caps. ``gh ... list --limit`` is invoked with the cap, and a
# returned collection whose length reaches the cap is treated as possibly
# truncated and therefore not trusted as a complete live report.
GH_PR_SAFETY_CAP = 201
GH_ISSUE_SAFETY_CAP = 201


def safety_cap_message(list_kind: str, cap: int, hint: str) -> str:
    """Return the standard ``gh`` saturation message.

    ``list_kind`` is the ``gh`` list noun (for example ``"pr"`` or ``"issue"``)
    and ``hint`` is the script-specific remediation guidance appended after the
    shared prefix. The shared prefix keeps the cap value and wording aligned
    across every live report.
    """
    return f"gh {list_kind} list reached the {cap} item safety cap; {hint}"
