from __future__ import annotations

from scripts.check_agents import _casefold_collisions


def test_casefold_collision_detection_flags_windows_checkout_conflicts() -> None:
    collisions = _casefold_collisions(
        [
            "AGENTS.md",
            "docs/AGENTS.md",
            "docs/agents.md",
            "app/main.py",
        ]
    )

    assert collisions == [["docs/AGENTS.md", "docs/agents.md"]]


def test_casefold_collision_detection_allows_distinct_agent_notes() -> None:
    collisions = _casefold_collisions(
        [
            "AGENTS.md",
            "docs/DOCS_AGENT_NOTES.md",
            "docs/agents.md",
            "app/webhooks/AGENTS.md",
        ]
    )

    assert collisions == []
