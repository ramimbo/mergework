from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

REQUIRED_TEMPLATE_SECTIONS = {
    "problem": ("problem", "current problem"),
    "evidence": ("evidence", "current evidence"),
    "proposed_work": ("proposed work", "proposal"),
    "value": ("value", "expected value"),
    "acceptance": ("acceptance", "verification", "test notes"),
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
        for award in bounty.get("awards", []) or []:
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


def analyze_proposed_work(data: dict[str, Any]) -> dict[str, Any]:
    payments = _payment_index(data)
    proposals = [
        proposal
        for raw in data.get("issues", [])
        if isinstance(raw, dict)
        for proposal in [_normalize_issue(raw, payments)]
        if proposal is not None
    ]
    related_groups = _related_groups(proposals)
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
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)


def load_live_issues(repo: str, limit: int) -> dict[str, Any]:
    rows = _run_gh(
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
            "label:proposed-work",
            "--json",
            "number",
        ]
    )
    issues: list[dict[str, Any]] = []
    for row in rows:
        number = row.get("number")
        if not isinstance(number, int):
            continue
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
    return {"issues": issues}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only proposed-work intake triage report")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="Offline JSON fixture with issues/payments")
    source.add_argument("--repo", help="GitHub repo for read-only gh live mode")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args(argv)

    data = (
        json.loads(args.input.read_text(encoding="utf-8"))
        if args.input
        else load_live_issues(args.repo, args.limit)
    )
    report = analyze_proposed_work(data)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
