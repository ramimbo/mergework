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
REQUIRED_PUBLIC_PHRASES = {
    "README.md": [
        "supported paths today are `github:*` balance claims",
        (
            "MergeWork does not currently operate a public BTC, USDC, fiat, "
            "bridge, exchange, or off-ramp."
        ),
        "require separate maintainer/contributor discussion before implementation",
    ],
    "docs/ledger.md": [
        "## Current Transfer Paths",
        "`github:*` balance claims into a linked wallet.",
        "Signed wallet-to-wallet transfers between registered wallets.",
        (
            "MergeWork does not currently operate a public BTC, USDC, fiat, "
            "bridge, exchange, or off-ramp."
        ),
        "require separate maintainer/contributor discussion before implementation",
    ],
    "docs/agent-guide.md": [
        ("Public reads such as `GET /api/v1/bounties/{id}/attempts` do not require login"),
        ("creating or releasing an attempt requires the GitHub-authenticated browser session"),
    ],
    "docs/paid-bounties.md": [
        "This page is not manually updated for every payout.",
        "https://mrwk.ltclab.site/activity",
        "https://api.mrwk.ltclab.site/api/v1/activity",
        "GET /api/v1/bounties/{id}",
        "GET /api/v1/proofs/{proof_hash}",
    ],
    "docs/bounty-rules.md": [
        "## Agent-Readable Bounty Post Template",
        "MRWK bounty: <amount> MRWK - <short scope>",
        "## Evidence or Tests Required",
        "## Out of Scope",
        "## Duplicate and Stale Work Rules",
        "GitHub issue search, the public bounty API, and MCP bounty tools",
        "## Submission Evidence Templates",
        "PR or fix claim:",
        "Review claim:",
        "Smoke-check or bug-report claim:",
        "Discussion or decision-support claim:",
        "Do not describe work as accepted, merged, or paid until the public GitHub label",
    ],
    "docs/api-examples.md": [
        "Internal ledger accounts use the same account response shape",
        ("Treasury and reserve balances change as bounties are reserved, paid, and released."),
    ],
}
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
DOCS_ISSUE_TEMPLATE = ".github/ISSUE_TEMPLATE/docs.yml"
PR_TEMPLATE = ".github/pull_request_template.md"


def _local_target_exists(source: Path, target: str) -> bool:
    clean = target.split("#", 1)[0]
    if not clean or clean.startswith(("http://", "https://", "mailto:")):
        return True
    return (source.parent / clean).resolve().exists()


def _squash(text: str) -> str:
    return " ".join(text.split())


def _template_field_block(template: str, field_id: str) -> str:
    marker = f"id: {field_id}"
    if marker not in template:
        return ""
    block = template.split(marker, 1)[1]
    next_field = block.find("\n    id: ")
    return block if next_field == -1 else block[:next_field]


def _template_field_is_required(template: str, field_id: str) -> bool:
    block = _template_field_block(template, field_id)
    return "validations:" in block and "required: true" in block


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
        squashed = _squash(text)
        for phrase in REQUIRED_PUBLIC_PHRASES.get(relative, []):
            if _squash(phrase) not in squashed:
                print(f"missing required public phrase in {relative}: {phrase}")
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
        print(f"missing pull request template: {PR_TEMPLATE}")
        ok = False
    elif "expected pr size:" not in pr_template.read_text(encoding="utf-8").lower():
        print("pull request template must ask for expected PR size")
        ok = False
    bounty_issue_template = ROOT / ".github/ISSUE_TEMPLATE/bounty.yml"
    if not bounty_issue_template.exists():
        print("missing bounty issue template: .github/ISSUE_TEMPLATE/bounty.yml")
        ok = False
    else:
        bounty_template = bounty_issue_template.read_text(encoding="utf-8").lower()
        for phrase in [
            "mrwk bounty: <amount> mrwk - <short scope>",
            "id: evidence",
            "evidence or tests required",
            "id: out_of_scope",
            "id: duplicate_stale_rules",
        ]:
            if phrase not in bounty_template:
                print(f"bounty issue template missing required phrase: {phrase}")
                ok = False
        for field_id in ("evidence", "out_of_scope", "duplicate_stale_rules"):
            if not _template_field_is_required(bounty_template, field_id):
                print(f"bounty issue template {field_id} field must be required")
                ok = False
    if ok:
        print("docs smoke ok")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
