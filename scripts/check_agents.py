from __future__ import annotations

import subprocess
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 12 * 1024
AGENT_NOTE_PATHS = [
    "AGENTS.md",
    "app/ledger/AGENTS.md",
    "app/webhooks/AGENTS.md",
    "docs/DOCS_AGENT_NOTES.md",
]


def _tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _casefold_collisions(paths: Iterable[str]) -> list[list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        groups[path.replace("\\", "/").casefold()].append(path)
    return [sorted(matches) for matches in groups.values() if len(matches) > 1]


def main() -> int:
    ok = True
    for collision in _casefold_collisions(_tracked_paths()):
        print("case-insensitive path collision: " + ", ".join(collision))
        ok = False
    for relative in AGENT_NOTE_PATHS:
        agents = ROOT / relative
        if not agents.exists():
            print(f"{relative} is missing")
            ok = False
            continue
        size = agents.stat().st_size
        if size > MAX_BYTES:
            print(f"{relative} is {size} bytes; limit is {MAX_BYTES}")
            ok = False
        else:
            print(f"{relative} ok ({size} bytes)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
