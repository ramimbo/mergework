from __future__ import annotations

import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 12 * 1024


def find_casefold_collisions(paths: list[str]) -> list[list[str]]:
    by_casefold: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        by_casefold[path.casefold()].append(path)
    return [sorted(matches) for matches in by_casefold.values() if len(matches) > 1]


def tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def agent_instruction_paths(paths: list[str]) -> list[str]:
    return sorted(path for path in paths if Path(path).name == "AGENTS.md")


def main() -> int:
    paths = tracked_paths()
    agent_paths = agent_instruction_paths(paths)
    if "AGENTS.md" not in agent_paths:
        print("AGENTS.md is missing")
        return 1
    ok = True
    for relative in agent_paths:
        agents = ROOT / relative
        size = agents.stat().st_size
        if size > MAX_BYTES:
            print(f"{relative} is {size} bytes; limit is {MAX_BYTES}")
            ok = False
    collisions = find_casefold_collisions(paths)
    if collisions:
        for collision in collisions:
            print("case-insensitive tracked path collision: " + ", ".join(collision))
        ok = False
    if not ok:
        return 1
    print(f"AGENTS.md files ok ({len(agent_paths)} checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
