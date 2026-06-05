from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_agents


def write_agent(root: Path, relative: str, content: bytes = b"ok") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


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


def test_agent_instruction_paths_include_nested_agents_files() -> None:
    paths = check_agents.agent_instruction_paths(
        ["AGENTS.md", "docs/AGENTS.md", "docs/agent-guide.md", "README.md"]
    )

    assert paths == ["AGENTS.md", "docs/AGENTS.md"]


def test_main_rejects_oversized_nested_agents_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_agent(tmp_path, "AGENTS.md")
    write_agent(tmp_path, "docs/AGENTS.md", b"x" * (check_agents.MAX_BYTES + 1))
    monkeypatch.setattr(check_agents, "ROOT", tmp_path)
    monkeypatch.setattr(
        check_agents,
        "tracked_paths",
        lambda: ["AGENTS.md", "docs/AGENTS.md", "docs/agent-guide.md"],
    )

    assert check_agents.main() == 1

    output = capsys.readouterr().out
    assert f"docs/AGENTS.md is {check_agents.MAX_BYTES + 1} bytes" in output


def test_main_reports_count_for_all_tracked_agents_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_agent(tmp_path, "AGENTS.md")
    write_agent(tmp_path, "docs/AGENTS.md")
    monkeypatch.setattr(check_agents, "ROOT", tmp_path)
    monkeypatch.setattr(
        check_agents,
        "tracked_paths",
        lambda: ["AGENTS.md", "docs/AGENTS.md", "README.md"],
    )

    assert check_agents.main() == 0

    output = capsys.readouterr().out
    assert "AGENTS.md files ok (2 checked)" in output
