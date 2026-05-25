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


def main() -> int:
    agents = ROOT / "AGENTS.md"
    if not agents.exists():
        print("AGENTS.md is missing")
        return 1
    size = agents.stat().st_size
    if size > MAX_BYTES:
        print(f"AGENTS.md is {size} bytes; limit is {MAX_BYTES}")
        return 1
    collisions = find_casefold_collisions(tracked_paths())
    if collisions:
        for collision in collisions:
            print("case-insensitive tracked path collision: " + ", ".join(collision))
        return 1
    print(f"AGENTS.md ok ({size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
