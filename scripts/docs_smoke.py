from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "docs/bounty-rules.md",
    "docs/paid-bounties.md",
    "docs/agent-guide.md",
    "docs/api-examples.md",
    "docs/ledger.md",
    "docs/admin-runbook.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
]
BANNED_PUBLIC_PHRASES = [
    "guaranteed market value",
    "promised convertibility",
    "1 MRWK = $",
]
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _local_target_exists(source: Path, target: str) -> bool:
    clean = target.split("#", 1)[0]
    if not clean or clean.startswith(("http://", "https://", "mailto:")):
        return True
    return (source.parent / clean).resolve().exists()


def main() -> int:
    ok = True
    for relative in REQUIRED:
        path = ROOT / relative
        if not path.exists():
            print(f"missing required doc: {relative}")
            ok = False
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for phrase in BANNED_PUBLIC_PHRASES:
            if phrase.lower() in lowered:
                print(f"banned public phrase in {relative}: {phrase}")
                ok = False
        for link in LINK_RE.findall(text):
            if not _local_target_exists(path, link):
                print(f"broken local link in {relative}: {link}")
                ok = False
    if ok:
        print("docs smoke ok")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
