from __future__ import annotations

from scripts import check_agents


def test_detects_case_insensitive_tracked_path_collisions() -> None:
    collisions = check_agents.find_casefold_collisions(
        ["docs/AGENTS.md", "docs/agents.md", "docs/ledger.md"]
    )

    assert collisions == [["docs/AGENTS.md", "docs/agents.md"]]


def test_non_colliding_agent_docs_are_allowed() -> None:
    collisions = check_agents.find_casefold_collisions(
        ["docs/AGENTS.md", "docs/agent-guide.md", "docs/ledger.md"]
    )

    assert collisions == []
