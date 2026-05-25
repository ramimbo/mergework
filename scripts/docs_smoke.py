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
DOCS_ISSUE_TEMPLATE = ".github/ISSUE_TEMPLATE/docs.yml"
PR_TEMPLATE = ".github/pull_request_template.md"
ISSUE_TEMPLATES = {
    ".github/ISSUE_TEMPLATE/bounty.yml",
    ".github/ISSUE_TEMPLATE/bug.yml",
    ".github/ISSUE_TEMPLATE/security-report.yml",
    ".github/ISSUE_TEMPLATE/docs.yml",
    DOCS_ISSUE_TEMPLATE,
}


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
    docs_issue_template = ROOT / DOCS_ISSUE_TEMPLATE
    if not docs_issue_template.exists():
        print(f"missing docs issue template: {DOCS_ISSUE_TEMPLATE}")
        ok = False
    else:
        template = docs_issue_template.read_text(encoding="utf-8").lower()
        if "id: location" not in template:
            print("docs issue template must ask where the unclear docs were seen")
            ok = False
        if "link the page, docs file, heading, command, or ui path" not in template:
            print("docs issue template location prompt must request actionable evidence")
            ok = False
    pr_template = ROOT / PR_TEMPLATE
    if not pr_template.exists():
        print(f"missing PR template: {PR_TEMPLATE}")
        ok = False
    if ok:
        print("docs smoke ok")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
