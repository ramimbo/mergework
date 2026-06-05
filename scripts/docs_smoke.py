from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "docs/bounty-lifecycle.md",
    "docs/bounty-rules.md",
    "docs/paid-bounties.md",
    "docs/agent-guide.md",
    "docs/api-examples.md",
    "docs/ledger.md",
    "docs/admin-runbook.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "docs/AGENTS.md",
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
    "CONTRIBUTING.md": [
        "Confirm the issue also has a `Reserved on MergeWork` comment",
        "pending `create_bounty` proposals",
        "a pending `pay_bounty` proposal is not paid work until a public proof exists",
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
        "Proposed work requests are intake issues, not live bounties",
        "wait for `mrwk:bounty`",
        "GET /api/v1/work-discovery",
        "claimable_now",
        "opening_soon",
        "Use [docs/bounty-lifecycle.md](bounty-lifecycle.md) as the short checklist",
    ],
    "docs/bounty-lifecycle.md": [
        "# Bounty Lifecycle",
        "A GitHub issue is claimable for MRWK only when",
        "`mrwk:bounty`",
        "Reserved on MergeWork",
        "A pending create_bounty proposal is not a live bounty.",
        "A pending pay_bounty proposal is not paid work.",
        "Confirm the requested submission artifact: PR, review, issue comment,",
        "result.github_issue_finalization",
    ],
    "docs/paid-bounties.md": [
        "This page is not manually updated for every payout.",
        "https://mrwk.online/activity",
        "https://api.mrwk.online/api/v1/activity",
        "Legacy-compatible endpoints remain available",
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
        "Non-live or stale bounty reference correction:",
        "Current public evidence link(s):",
        "Do not describe work as accepted, merged, or paid until the public GitHub label",
        "## Proposed Work Requests",
        "proposed issue -> maintainer review -> optional create_bounty proposal",
        "Reference tiers are guidance, not entitlement",
        "For the concise state machine and maintainer checklist",
    ],
    "docs/api-examples.md": [
        "API_HOST=https://api.mrwk.online",
        "MCP_HOST=https://mcp.mrwk.online",
        "https://api.mrwk.ltclab.site",
        "https://mcp.mrwk.ltclab.site",
        "Internal ledger accounts use the same account response shape",
        "effective_awards_remaining",
        "availability_state",
        "effective_awards_remaining` is zero",
        "availability_state` is not",
        "pending payout proposals as proof-backed paid work",
        "/api/v1/work-discovery",
        "live bounty rows from pending create-bounty proposals",
        ("Treasury and reserve balances change as bounties are reserved, paid, and released."),
    ],
    "docs/admin-runbook.md": [
        "MERGEWORK_TREASURY_EXECUTOR_ENABLED=1",
        "uses the production `.env`",
        "docker compose logs -f treasury-executor",
        "Verify `result.github_issue_finalization`",
        "Accepted claims queued as pending `pay_bounty` proposals",
        'Do not write "paid", "settled", "received", or "withdrawable"',
    ],
    "SECURITY.md": [
        (
            "A pending `pay_bounty` proposal means the security work was "
            "accepted for payout review, not paid."
        ),
        "A security bounty is paid only after a public proof or ledger entry exists.",
        "Public comments should link the redacted proof",
    ],
    "docs/AGENTS.md": [
        "Before documenting a bounty as claimable, confirm `mrwk:bounty`",
        "remaining effective award capacity",
        "Do not submit or encourage `/claim`",
        "pending `pay_bounty` proposals as accepted-for-review",
    ],
}
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FIELD_ID_RE = re.compile(r"^(?P<indent>\s*)id:\s*(?P<field_id>[^\s#]+)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
DOCS_ISSUE_TEMPLATE = ".github/ISSUE_TEMPLATE/docs.yml"
SECURITY_ISSUE_TEMPLATE = ".github/ISSUE_TEMPLATE/security-report.yml"
BUG_ISSUE_TEMPLATE = ".github/ISSUE_TEMPLATE/bug.yml"
PR_TEMPLATE = ".github/pull_request_template.md"


def _markdown_heading_anchor(heading: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", heading.strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s-]+", "-", text).strip("-")


def _markdown_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    fence_marker = ""

    for line in path.read_text(encoding="utf-8").splitlines():
        fence_match = FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not fence_marker:
                fence_marker = marker
            elif marker.startswith(fence_marker[0]) and len(marker) >= len(fence_marker):
                fence_marker = ""
            continue
        if fence_marker:
            continue

        heading_match = HEADING_RE.match(line)
        if not heading_match:
            continue
        anchor = _markdown_heading_anchor(heading_match.group(1))
        count = counts.get(anchor, 0)
        counts[anchor] = count + 1
        anchors.add(anchor if count == 0 else f"{anchor}-{count}")

    return anchors


def _local_target_exists(source: Path, target: str) -> bool:
    clean, separator, fragment = target.partition("#")
    if clean.startswith(("http://", "https://", "mailto:")):
        return True
    target_path = (source.parent / clean).resolve() if clean else source.resolve()
    if not target_path.exists():
        return False
    if separator and fragment and target_path.suffix.lower() in {".md", ".markdown"}:
        return fragment in _markdown_anchors(target_path)
    return True


def _squash(text: str) -> str:
    return " ".join(text.split())


def _template_field_block(template: str, field_id: str) -> str:
    lines = template.splitlines()
    for index, line in enumerate(lines):
        match = FIELD_ID_RE.match(line)
        if not match or match.group("field_id").strip("\"'") != field_id:
            continue
        indent = match.group("indent")
        end = len(lines)
        for next_index, next_line in enumerate(lines[index + 1 :], start=index + 1):
            next_match = FIELD_ID_RE.match(next_line)
            if next_match and next_match.group("indent") == indent:
                end = next_index
                break
        return "\n".join(lines[index:end])
    return ""


def _template_field_is_required(template: str, field_id: str) -> bool:
    block = _template_field_block(template, field_id)
    return "validations:" in block and "required: true" in block


def _issue_template_labels(template: str) -> set[str]:
    labels: set[str] = set()
    lines = template.splitlines()

    def add_labels(raw_value: str) -> None:
        value = raw_value.split("#", 1)[0].strip()
        if not value:
            return
        value = value.strip("[]")
        for part in value.split(","):
            label = part.strip().strip("\"'")
            if label:
                labels.add(label.lower())

    for index, line in enumerate(lines):
        if not line.startswith("labels:"):
            continue
        add_labels(line.split(":", 1)[1])
        for continuation in lines[index + 1 :]:
            if not continuation.strip():
                continue
            if not continuation.startswith((" ", "\t")):
                break
            stripped = continuation.strip()
            if stripped.startswith("- "):
                add_labels(stripped[2:])
        break
    return labels


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
    security_issue_template = ROOT / SECURITY_ISSUE_TEMPLATE
    if not security_issue_template.exists():
        print(f"missing security issue template: {SECURITY_ISSUE_TEMPLATE}")
        ok = False
    else:
        security_template = security_issue_template.read_text(encoding="utf-8").lower()
        for phrase in [
            "do not paste exploit details here",
            "follow security.md for private reporting",
            "public-safe summary",
        ]:
            if phrase not in security_template:
                print(f"security issue template missing required phrase: {phrase}")
                ok = False
        if "security" not in _issue_template_labels(security_template):
            print("security issue template must auto-apply the security label")
            ok = False
        if not _template_field_is_required(security_template, "summary"):
            print("security issue template summary field must be required")
            ok = False
        if "mrwk:bounty" in _issue_template_labels(security_template):
            print("security issue template must not auto-apply mrwk:bounty")
            ok = False
    bug_issue_template = ROOT / BUG_ISSUE_TEMPLATE
    if not bug_issue_template.exists():
        print(f"missing bug issue template: {BUG_ISSUE_TEMPLATE}")
        ok = False
    else:
        bug_template = bug_issue_template.read_text(encoding="utf-8").lower()
        if "bug" not in _issue_template_labels(bug_template):
            print("bug issue template must auto-apply the bug label")
            ok = False
        for field_id in ("expected", "actual", "reproduce"):
            if not _template_field_is_required(bug_template, field_id):
                print(f"bug issue template {field_id} field must be required")
                ok = False
        for phrase in ["expected behavior", "actual behavior", "reproduction"]:
            if phrase not in bug_template:
                print(f"bug issue template missing required phrase: {phrase}")
                ok = False
        if "mrwk:bounty" in _issue_template_labels(bug_template):
            print("bug issue template must not auto-apply mrwk:bounty")
            ok = False
    bounty_issue_template = ROOT / ".github/ISSUE_TEMPLATE/bounty.yml"
    if not bounty_issue_template.exists():
        print("missing bounty issue template: .github/ISSUE_TEMPLATE/bounty.yml")
        ok = False
    else:
        bounty_template = bounty_issue_template.read_text(encoding="utf-8").lower()
        for phrase in [
            "mrwk bounty: <amount> mrwk - <short scope>",
            "do not add the live bounty label from this template",
            "treasury proposal executes",
            "maintainers add `mrwk:bounty`",
            "reserved on mergework",
            "open public bounty row",
            "award capacity remains after accepted or pending payout work",
            "id: evidence",
            "evidence or tests required",
            "id: out_of_scope",
            "id: duplicate_stale_rules",
        ]:
            if phrase not in bounty_template:
                print(f"bounty issue template missing required phrase: {phrase}")
                ok = False
        if "mrwk:bounty" in _issue_template_labels(bounty_template):
            print("bounty issue template must not auto-apply mrwk:bounty")
            ok = False
        for field_id in ("evidence", "out_of_scope", "duplicate_stale_rules"):
            if not _template_field_is_required(bounty_template, field_id):
                print(f"bounty issue template {field_id} field must be required")
                ok = False
    proposed_work_template = ROOT / ".github/ISSUE_TEMPLATE/proposed-work.yml"
    if not proposed_work_template.exists():
        print("missing proposed work issue template: .github/ISSUE_TEMPLATE/proposed-work.yml")
        ok = False
    else:
        proposed_template = proposed_work_template.read_text(encoding="utf-8").lower()
        for phrase in [
            'title: "proposed work: <short scope>"',
            'labels: ["proposed-work"]',
            "not a live mrwk bounty",
            "do not submit `/claim`",
            "id: related_bounty_or_source",
            "related bounty or source issue",
            "link the intake bounty, source issue, pr, discussion, api route, docs page",
            "id: duplicate_search",
        ]:
            if phrase not in proposed_template:
                print(f"proposed work issue template missing required phrase: {phrase}")
                ok = False
        if not _template_field_is_required(proposed_template, "related_bounty_or_source"):
            print("proposed work issue template related_bounty_or_source field must be required")
            ok = False
        if "mrwk:bounty" in proposed_template:
            print("proposed work issue template must not mention or apply mrwk:bounty")
            ok = False
    if ok:
        print("docs smoke ok")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
