from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

REQUIRED_TEMPLATE_SECTIONS = {
    "problem": ("problem", "current problem"),
    "evidence": ("evidence", "current evidence"),
    "proposed_work": ("proposed work", "proposal"),
    "value": ("value", "expected value"),
    "acceptance": ("acceptance", "acceptance criteria", "possible acceptance criteria"),
    "tests": (
        "tests",
        "evidence or tests required",
        "tests required",
        "test notes",
        "verification",
    ),
    "duplicate_search": ("duplicate search", "duplicates"),
    "out_of_scope": ("out of scope",),
}
ROUTED_RE = re.compile(
    r"\b(accepted by|accepted and|routed|created bounty|create_bounty|"
    r"treasury proposal|reserved on mergework)\b",
    re.IGNORECASE,
)
REJECTED_RE = re.compile(
    r"\b(rejected|declined|not accepted|outside accepted scope)\b", re.IGNORECASE
)
NON_LIVE_CONFUSION_RE = re.compile(
    r"(claimable now|already paid|guaranteed payout|cash[- ]?out|off[- ]?ramp)",
    re.IGNORECASE,
)
BOUNTY_TITLE_RE = re.compile(r"^\s*MRWK bounty\s*:", re.IGNORECASE)
BOUNTY_TEMPLATE_HEADING_RE = re.compile(r"(^|\n)\s*#+\s*MRWK bounty\b", re.IGNORECASE)
WORD_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "add",
    "and",
    "bounty",
    "for",
    "from",
    "issue",
    "mrwk",
    "proposed",
    "request",
    "the",
    "to",
    "work",
}
GH_TIMEOUT_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 30
DEFAULT_API_HOST = "https://api.mrwk.online"
DEFAULT_PAYMENT_BOUNTY_ISSUE_NUMBERS = (649,)
LIVE_ISSUE_SEARCHES = (
    "label:proposed-work",
    '"proposed work"',
)
READ_ONLY_GH_COMMANDS = {
    ("issue", "list"),
    ("issue", "view"),
}


def _labels(raw: dict[str, Any]) -> list[str]:
    labels = raw.get("labels", [])
    names: list[str] = []
    for label in labels:
        if isinstance(label, str):
            names.append(label)
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            names.append(label["name"])
    return names


def _comments(raw: dict[str, Any]) -> list[str]:
    comments = raw.get("comments", [])
    bodies: list[str] = []
    for comment in comments:
        if isinstance(comment, str):
            bodies.append(comment)
        elif isinstance(comment, dict) and isinstance(comment.get("body"), str):
            bodies.append(comment["body"])
    return bodies


def _combined_text(issue: dict[str, Any]) -> str:
    parts = [str(issue.get("title") or ""), str(issue.get("body") or "")]
    parts.extend(_comments(issue))
    return "\n".join(parts)


def _is_non_intake_bounty_issue(issue: dict[str, Any]) -> bool:
    labels = {label.lower() for label in _labels(issue)}
    if "mrwk:bounty" in labels:
        return True
    title = str(issue.get("title") or "")
    if BOUNTY_TITLE_RE.search(title):
        return True
    body = str(issue.get("body") or "")
    return bool(BOUNTY_TEMPLATE_HEADING_RE.search(body))


def _has_section(body: str, aliases: tuple[str, ...]) -> bool:
    lowered = body.lower()
    for alias in aliases:
        if re.search(rf"(^|\n)\s*#+\s*{re.escape(alias)}\b", lowered):
            return True
        if re.search(rf"(^|\n)\s*(?:-\s*)?\*\*{re.escape(alias)}\*\*", lowered):
            return True
    return False


def _missing_sections(body: str) -> list[str]:
    return [
        key
        for key, aliases in REQUIRED_TEMPLATE_SECTIONS.items()
        if not _has_section(body, aliases)
    ]


def _token_set(issue: dict[str, Any]) -> set[str]:
    text = str(issue.get("title") or "").lower()
    return {word for word in WORD_RE.findall(text) if len(word) > 3 and word not in STOPWORDS}


def _is_vague(body: str, missing_sections: list[str]) -> bool:
    return len(body.split()) < 45 or len(missing_sections) >= 4


def _has_non_live_confusion(text: str) -> bool:
    for line in text.splitlines():
        lowered = line.lower()
        is_guardrail = (
            "do not" in lowered or "don't" in lowered or lowered.lstrip().startswith("- no ")
        )
        if "/claim" in lowered and not is_guardrail:
            return True
        if NON_LIVE_CONFUSION_RE.search(lowered) and not is_guardrail:
            return True
    return False


def _payment_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in data.get("payments", []):
        if not isinstance(item, dict):
            continue
        submission_url = item.get("submission_url")
        if isinstance(submission_url, str) and submission_url:
            index[submission_url.rstrip("/")] = item
    for bounty in data.get("bounties", []):
        if not isinstance(bounty, dict):
            continue
        for proposal in bounty.get("pending_payout_proposals", []) or []:
            if not isinstance(proposal, dict):
                continue
            submission_url = proposal.get("submission_url")
            if isinstance(submission_url, str) and submission_url:
                index[submission_url.rstrip("/")] = {
                    "state": "pending",
                    "source": "pending_payout_proposal",
                    "proposal_id": proposal.get("proposal_id"),
                    "accepted_by": proposal.get("accepted_by"),
                    "executes_after": proposal.get("executes_after"),
                }
        awards: list[Any] = []
        for award_key in ("awards", "accepted_awards"):
            raw_awards = bounty.get(award_key) or []
            if isinstance(raw_awards, list):
                awards.extend(raw_awards)
        for award in awards:
            if not isinstance(award, dict):
                continue
            submission_url = award.get("submission_url")
            if isinstance(submission_url, str) and submission_url:
                index[submission_url.rstrip("/")] = {
                    "state": "paid",
                    "source": "proof_backed_award",
                    "proof_url": award.get("proof_url"),
                    "ledger_sequence": award.get("ledger_sequence"),
                }
    return index


def _payment_status(issue: dict[str, Any], payments: dict[str, dict[str, Any]]) -> dict[str, Any]:
    issue_url = str(issue.get("url") or "").rstrip("/")
    payment = payments.get(issue_url)
    if payment:
        return payment
    for comment in _comments(issue):
        for url in re.findall(r"https://github\.com/[^\s)]+", comment):
            payment = payments.get(url.rstrip("/"))
            if payment:
                return payment
    return {"state": "none"}


def _normalize_issue(
    raw: dict[str, Any], payments: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    number = raw.get("number")
    if not isinstance(number, int):
        return None
    body = str(raw.get("body") or "")
    labels = _labels(raw)
    missing = _missing_sections(body)
    text = _combined_text(raw)
    warnings: list[str] = []
    if "proposed-work" not in {label.lower() for label in labels}:
        warnings.append("missing_proposed_work_label")
    if missing:
        warnings.append("missing_template_sections")
    if _is_vague(body, missing):
        warnings.append("vague_or_under_specified")
    if ROUTED_RE.search(text):
        warnings.append("already_routed_or_accepted")
    if REJECTED_RE.search(text):
        warnings.append("rejected_or_out_of_scope")
    if _has_non_live_confusion(text):
        warnings.append("non_live_bounty_confusion")
    payment = _payment_status(raw, payments)
    if payment.get("state") == "pending":
        warnings.append("accepted_pending_payout")
    elif payment.get("state") == "paid":
        warnings.append("proof_backed_paid")
    return {
        "number": number,
        "title": str(raw.get("title") or ""),
        "url": raw.get("url"),
        "state": str(raw.get("state") or ""),
        "labels": labels,
        "missing_sections": missing,
        "warnings": warnings,
        "payment_status": payment,
        "tokens": sorted(_token_set(raw)),
    }


def _related_groups(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], set[int]] = defaultdict(set)
    for index, left in enumerate(proposals):
        left_tokens = set(left["tokens"])
        if len(left_tokens) < 3:
            continue
        for right in proposals[index + 1 :]:
            right_tokens = set(right["tokens"])
            common = left_tokens & right_tokens
            if len(common) < 3:
                continue
            if len(common) / min(len(left_tokens), len(right_tokens)) < 0.6:
                continue
            grouped[tuple(sorted(common))].update({left["number"], right["number"]})
    groups: list[dict[str, Any]] = []
    for tokens, numbers in grouped.items():
        if len(numbers) < 2:
            continue
        groups.append(
            {
                "issues": sorted(numbers),
                "evidence_tokens": list(tokens),
                "suggested_scope": " / ".join(tokens[:6]),
            }
        )
    return sorted(groups, key=lambda item: (-len(item["issues"]), item["issues"]))


def _mark_duplicate_warnings(
    proposals: list[dict[str, Any]], related_groups: list[dict[str, Any]]
) -> None:
    grouped_numbers = {
        number
        for group in related_groups
        for number in group.get("issues", [])
        if isinstance(number, int)
    }
    for proposal in proposals:
        if (
            proposal["number"] in grouped_numbers
            and "duplicate_looking_related_proposal" not in proposal["warnings"]
        ):
            proposal["warnings"].append("duplicate_looking_related_proposal")


def analyze_proposed_work(data: dict[str, Any]) -> dict[str, Any]:
    payments = _payment_index(data)
    data_warnings = [
        warning for warning in data.get("data_warnings", []) if isinstance(warning, str)
    ]
    proposals = [
        proposal
        for raw in data.get("issues", [])
        if isinstance(raw, dict) and not _is_non_intake_bounty_issue(raw)
        for proposal in [_normalize_issue(raw, payments)]
        if proposal is not None
    ]
    related_groups = _related_groups(proposals)
    _mark_duplicate_warnings(proposals, related_groups)
    warning_counts: dict[str, int] = defaultdict(int)
    payment_counts: dict[str, int] = defaultdict(int)
    for proposal in proposals:
        payment_counts[str(proposal["payment_status"].get("state") or "none")] += 1
        for warning in proposal["warnings"]:
            warning_counts[warning] += 1
    return {
        "summary": {
            "proposed_work_issues": len(proposals),
            "warning_counts": dict(sorted(warning_counts.items())),
            "payment_counts": dict(sorted(payment_counts.items())),
            "related_groups": len(related_groups),
            "data_warnings": data_warnings,
        },
        "proposals": proposals,
        "related_groups": related_groups,
    }


def format_markdown(report: dict[str, Any]) -> str:
    lines = ["# Proposed Work Triage", ""]
    summary = report["summary"]
    lines.append(f"- proposed work issues: {summary['proposed_work_issues']}")
    lines.append(f"- related groups: {summary['related_groups']}")
    for state, count in summary["payment_counts"].items():
        lines.append(f"- {state} payment status: {count}")
    for warning in summary.get("data_warnings", []):
        lines.append(f"- data warning: {warning}")
    lines.append("")
    lines.append("## Issues")
    for item in report["proposals"]:
        warnings = ", ".join(item["warnings"]) if item["warnings"] else "none"
        missing = ", ".join(item["missing_sections"]) if item["missing_sections"] else "none"
        payment = item["payment_status"].get("state", "none")
        lines.append(f"- #{item['number']} {item['title']} ({payment})")
        lines.append(f"  - warnings: {warnings}")
        lines.append(f"  - missing sections: {missing}")
        if item.get("url"):
            lines.append(f"  - url: {item['url']}")
    if report["related_groups"]:
        lines.append("")
        lines.append("## Related Groups")
        for group in report["related_groups"]:
            issues = ", ".join(f"#{number}" for number in group["issues"])
            evidence = ", ".join(group["evidence_tokens"])
            lines.append(f"- {issues}: {group['suggested_scope']} ({evidence})")
    return "\n".join(lines)


def _run_gh(args: list[str]) -> Any:
    if len(args) < 2 or tuple(args[:2]) not in READ_ONLY_GH_COMMANDS:
        allowed = ", ".join(" ".join(command) for command in sorted(READ_ONLY_GH_COMMANDS))
        raise RuntimeError(f"live mode only permits read-only gh commands: {allowed}")
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"gh command timed out after {GH_TIMEOUT_SECONDS}s") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        excerpt = result.stdout[:200].strip()
        raise RuntimeError(f"gh returned invalid JSON: {excerpt}") from exc


def _gh_issue_search(repo: str, query: str, limit: int) -> list[dict[str, Any]]:
    return _run_gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            str(limit),
            "--search",
            query,
            "--json",
            "number",
        ]
    )


def _load_public_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "mergework-proposed-work-triage"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.load(response)


def _load_public_bounty_issue(
    api_host: str, issue_number: int
) -> tuple[list[dict[str, Any]], list[str]]:
    query = urllib.parse.urlencode({"issue_number": str(issue_number), "limit": "5"})
    warnings: list[str] = []
    try:
        rows = _load_public_json(f"{api_host.rstrip('/')}/api/v1/bounties?{query}")
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        warnings.append(
            "payment_state_incomplete: failed to load public bounty list "
            f"for issue #{issue_number} ({type(exc).__name__})"
        )
        return [], warnings
    bounties: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        bounty_id = row.get("id")
        if not isinstance(bounty_id, int):
            continue
        try:
            detail = _load_public_json(f"{api_host.rstrip('/')}/api/v1/bounties/{bounty_id}")
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            warnings.append(
                "payment_state_incomplete: failed to load public bounty "
                f"detail for bounty {bounty_id}; using list row only ({type(exc).__name__})"
            )
            detail = row
        if isinstance(detail, dict):
            bounties.append(detail)
    return bounties, warnings


def _load_public_bounty_issues(
    api_host: str, issue_numbers: list[int] | tuple[int, ...]
) -> tuple[list[dict[str, Any]], list[str]]:
    bounties: list[dict[str, Any]] = []
    warnings: list[str] = []
    for issue_number in issue_numbers:
        issue_bounties, issue_warnings = _load_public_bounty_issue(api_host, issue_number)
        bounties.extend(issue_bounties)
        warnings.extend(issue_warnings)
    return bounties, warnings


def _positive_issue_number(value: str) -> int:
    try:
        issue_number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("payment bounty issue must be an integer") from exc
    if issue_number <= 0:
        raise argparse.ArgumentTypeError("payment bounty issue must be positive")
    return issue_number


def load_live_issues(
    repo: str,
    limit: int,
    api_host: str = DEFAULT_API_HOST,
    payment_bounty_issue_numbers: list[int] | tuple[int, ...] = (
        DEFAULT_PAYMENT_BOUNTY_ISSUE_NUMBERS
    ),
) -> dict[str, Any]:
    rows_by_number: dict[int, dict[str, Any]] = {}
    per_search_limit = max(1, limit)
    for query in LIVE_ISSUE_SEARCHES:
        for row in _gh_issue_search(repo, query, per_search_limit):
            number = row.get("number")
            if isinstance(number, int):
                rows_by_number[number] = row
    issues: list[dict[str, Any]] = []
    for number in sorted(rows_by_number, reverse=True)[:limit]:
        issue = _run_gh(
            [
                "issue",
                "view",
                str(number),
                "--repo",
                repo,
                "--comments",
                "--json",
                "number,title,url,body,labels,state,comments,author,createdAt,updatedAt",
            ]
        )
        issues.append(issue)
    bounties, data_warnings = _load_public_bounty_issues(api_host, payment_bounty_issue_numbers)
    return {
        "issues": issues,
        "bounties": bounties,
        "data_warnings": data_warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only proposed-work intake triage report")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="Offline JSON fixture with issues/payments")
    source.add_argument("--repo", help="GitHub repo for read-only gh live mode")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--api-host", default=DEFAULT_API_HOST)
    parser.add_argument(
        "--payment-bounty-issue",
        action="append",
        default=None,
        type=_positive_issue_number,
        help=(
            "GitHub issue number for an accepted-proposed-work bounty whose public "
            "payment state should be loaded. Repeat during round transitions."
        ),
    )
    args = parser.parse_args(argv)
    if args.input and args.payment_bounty_issue:
        parser.error("--payment-bounty-issue is only valid in live --repo mode")

    data = (
        json.loads(args.input.read_text(encoding="utf-8"))
        if args.input
        else load_live_issues(
            args.repo,
            args.limit,
            api_host=args.api_host,
            payment_bounty_issue_numbers=(
                args.payment_bounty_issue or list(DEFAULT_PAYMENT_BOUNTY_ISSUE_NUMBERS)
            ),
        )
    )
    report = analyze_proposed_work(data)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
