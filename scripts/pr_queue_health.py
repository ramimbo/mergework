from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bounty_refs import BOUNTY_REF_RE

NOISY_TITLE_PREFIX_RE = re.compile(r"^\s*(?:\[[^\]]+\]\s*)+")
UNSTABLE_MERGE_STATES = {"blocked", "conflicting", "dirty", "unknown", "unstable"}
GH_TIMEOUT_SECONDS = 30
GH_PR_SAFETY_CAP = 201
GH_ISSUE_SAFETY_CAP = 201
MAX_BOUNTY_REF = 2**63 - 1


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


def _merge_state(raw: dict[str, Any]) -> str:
    for key in ("merge_state", "mergeStateStatus", "mergeable", "mergeable_state"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    return "unknown"


def _scope_key(raw: dict[str, Any]) -> str:
    explicit = raw.get("scope")
    if isinstance(explicit, str) and explicit.strip():
        return " ".join(explicit.lower().split())
    title = str(raw.get("title") or "")
    title = NOISY_TITLE_PREFIX_RE.sub("", title)
    return " ".join(title.lower().split())


def _bounty_refs(raw: dict[str, Any]) -> list[int]:
    explicit = raw.get("bounty_refs")
    if isinstance(explicit, list):
        refs = [item for item in explicit if isinstance(item, int)]
        if refs:
            return sorted(set(refs))
    text = "\n".join(
        str(raw.get(key) or "")
        for key in ("title", "body", "description")
        if raw.get(key) is not None
    )
    found_refs: set[int] = set()
    for match in BOUNTY_REF_RE.findall(text):
        try:
            ref = int(match)
        except ValueError:
            continue
        if ref <= MAX_BOUNTY_REF:
            found_refs.add(ref)
    return sorted(found_refs)


def _is_open_bounty(raw: dict[str, Any]) -> bool:
    state = str(raw.get("state") or "").lower()
    remaining = raw.get("awards_remaining", raw.get("awardsRemaining"))
    if state and state != "open":
        return False
    if remaining is not None:
        try:
            return int(remaining) > 0
        except (TypeError, ValueError):
            return False
    return state == "open"


def _bounty_liveness(raw: dict[str, Any]) -> tuple[bool, str]:
    if not _is_open_bounty(raw):
        return False, "closed or exhausted"
    if "labels" in raw and not any(label.lower() == "mrwk:bounty" for label in _labels(raw)):
        return False, "missing mrwk:bounty label"
    if "comments" in raw and not any("Reserved on MergeWork:" in body for body in _comments(raw)):
        return False, "missing Reserved on MergeWork claims-open comment"
    return True, "live"


def _issue(pr: dict[str, Any], reason: str, detail: str) -> dict[str, Any]:
    return {
        "pull_request": pr["number"],
        "title": pr["title"],
        "url": pr.get("url"),
        "reason": reason,
        "detail": detail,
    }


def analyze_queue(data: dict[str, Any]) -> dict[str, Any]:
    bounties = {
        int(item["number"]): item
        for item in data.get("bounties", [])
        if isinstance(item, dict) and isinstance(item.get("number"), int)
    }
    prs = [item for item in data.get("pull_requests", []) if isinstance(item, dict)]
    normalized_prs: list[dict[str, Any]] = []
    for pr in prs:
        if not isinstance(pr.get("number"), int):
            continue
        normalized_prs.append(
            {
                "number": int(pr["number"]),
                "title": str(pr.get("title") or ""),
                "url": pr.get("url"),
                "refs": _bounty_refs(pr),
                "labels": _labels(pr),
                "merge_state": _merge_state(pr),
                "scope": _scope_key(pr),
            }
        )

    closed_bounty_references: list[dict[str, Any]] = []
    non_live_bounty_references: list[dict[str, Any]] = []
    missing_bounty_references: list[dict[str, Any]] = []
    dirty_or_unstable_merge_state: list[dict[str, Any]] = []
    needs_info: list[dict[str, Any]] = []
    duplicate_groups: dict[tuple[int, str], list[int]] = defaultdict(list)

    for pr in normalized_prs:
        if not pr["refs"]:
            missing_bounty_references.append(
                _issue(
                    pr,
                    "missing_bounty_reference",
                    "No bounty reference such as Bounty #<issue>, Refs #<issue>, "
                    "Fixes #<issue>, or /claim #<issue> found",
                )
            )
        for ref in pr["refs"]:
            bounty = bounties.get(ref)
            if bounty is None:
                closed_bounty_references.append(
                    _issue(
                        pr,
                        "unknown_bounty_reference",
                        f"Referenced bounty #{ref} was not in input",
                    )
                )
            elif not _is_open_bounty(bounty):
                closed_bounty_references.append(
                    _issue(
                        pr,
                        "closed_or_exhausted_bounty",
                        f"Referenced bounty #{ref} is not payable",
                    )
                )
            else:
                is_live, reason = _bounty_liveness(bounty)
                if not is_live:
                    non_live_bounty_references.append(
                        _issue(
                            pr,
                            "non_live_bounty_reference",
                            f"Referenced bounty #{ref} is not live claimable: {reason}",
                        )
                    )
            duplicate_groups[(ref, pr["scope"])].append(pr["number"])
        if pr["merge_state"] in UNSTABLE_MERGE_STATES:
            dirty_or_unstable_merge_state.append(
                _issue(pr, "dirty_or_unstable_merge_state", f"Merge state is {pr['merge_state']}")
            )
        if any(label.lower() == "mrwk:needs-info" for label in pr["labels"]):
            needs_info.append(_issue(pr, "mrwk_needs_info", "PR has mrwk:needs-info label"))

    duplicate_scope_groups = [
        {"bounty": bounty, "scope": scope, "pull_requests": sorted(numbers)}
        for (bounty, scope), numbers in sorted(duplicate_groups.items())
        if len(numbers) > 1 and scope
    ]
    closed_or_exhausted_count = sum(
        1 for bounty in bounties.values() if not _is_open_bounty(bounty)
    )
    live_bounty_count = sum(1 for bounty in bounties.values() if _bounty_liveness(bounty)[0])
    non_live_bounty_count = sum(
        1
        for bounty in bounties.values()
        if _is_open_bounty(bounty) and not _bounty_liveness(bounty)[0]
    )
    report = {
        "summary": {
            "pull_requests": len(normalized_prs),
            "open_bounties": len(bounties) - closed_or_exhausted_count,
            "live_bounties": live_bounty_count,
            "non_live_bounties": non_live_bounty_count,
            "closed_or_exhausted_bounties": closed_or_exhausted_count,
            "closed_bounty_references": len(closed_bounty_references),
            "non_live_bounty_references": len(non_live_bounty_references),
            "missing_bounty_references": len(missing_bounty_references),
            "dirty_or_unstable_merge_state": len(dirty_or_unstable_merge_state),
            "needs_info": len(needs_info),
            "duplicate_scope_groups": len(duplicate_scope_groups),
        },
        "closed_bounty_references": closed_bounty_references,
        "non_live_bounty_references": non_live_bounty_references,
        "missing_bounty_references": missing_bounty_references,
        "dirty_or_unstable_merge_state": dirty_or_unstable_merge_state,
        "needs_info": needs_info,
        "duplicate_scope_groups": duplicate_scope_groups,
    }
    return report


def has_queue_issues(report: dict[str, Any]) -> bool:
    return any(
        report[key]
        for key in (
            "closed_bounty_references",
            "non_live_bounty_references",
            "missing_bounty_references",
            "dirty_or_unstable_merge_state",
            "needs_info",
            "duplicate_scope_groups",
        )
    )


def format_text_report(report: dict[str, Any]) -> str:
    lines = ["PR queue health summary"]
    for key, value in report["summary"].items():
        lines.append(f"- {key.replace('_', ' ')}: {value}")
    if not has_queue_issues(report):
        lines.append("")
        lines.append("No queue-health issues found.")
        return "\n".join(lines)
    sections = [
        ("Closed or exhausted bounty references", "closed_bounty_references"),
        ("Non-live bounty references", "non_live_bounty_references"),
        ("Missing bounty references", "missing_bounty_references"),
        ("Dirty or unstable merge state", "dirty_or_unstable_merge_state"),
        ("Needs info", "needs_info"),
    ]
    for title, key in sections:
        if report[key]:
            lines.append("")
            lines.append(title)
            for item in report[key]:
                lines.append(f"- PR #{item['pull_request']}: {item['title']} ({item['detail']})")
    if report["duplicate_scope_groups"]:
        lines.append("")
        lines.append("Likely duplicate bounty scope")
        for item in report["duplicate_scope_groups"]:
            prs = ", ".join(f"#{number}" for number in item["pull_requests"])
            lines.append(f"- Bounty #{item['bounty']}: {item['scope']} ({prs})")
    return "\n".join(lines)


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _markdown_pr_issue(item: dict[str, Any]) -> str:
    pr_label = f"PR #{item['pull_request']}"
    url = item.get("url")
    if isinstance(url, str) and url:
        pr_label = f"[{pr_label}]({url})"
    return f"- {pr_label}: {_single_line(item['title'])} ({_single_line(item['detail'])})"


def format_markdown_report(report: dict[str, Any]) -> str:
    lines = ["## PR Queue Health Summary", ""]
    for key, value in report["summary"].items():
        lines.append(f"- **{key.replace('_', ' ')}**: {value}")
    if not has_queue_issues(report):
        lines.append("")
        lines.append("No queue-health issues found.")
        return "\n".join(lines)

    sections = [
        ("Closed or exhausted bounty references", "closed_bounty_references"),
        ("Non-live bounty references", "non_live_bounty_references"),
        ("Missing bounty references", "missing_bounty_references"),
        ("Dirty or unstable merge state", "dirty_or_unstable_merge_state"),
        ("Needs info", "needs_info"),
    ]
    for title, key in sections:
        if report[key]:
            lines.append("")
            lines.append(f"### {title}")
            for item in report[key]:
                lines.append(_markdown_pr_issue(item))
    if report["duplicate_scope_groups"]:
        lines.append("")
        lines.append("### Likely duplicate bounty scope")
        for item in report["duplicate_scope_groups"]:
            prs = ", ".join(f"#{number}" for number in item["pull_requests"])
            lines.append(f"- Bounty #{item['bounty']}: {_single_line(item['scope'])} ({prs})")
    return "\n".join(lines)


def _run_gh_json(args: list[str]) -> Any:
    command = " ".join(args)
    try:
        completed = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"gh command timed out after {GH_TIMEOUT_SECONDS}s: {command}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "gh command failed "
            f"(exit {exc.returncode}): {command}\n"
            f"stdout:\n{exc.stdout or exc.output or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        ) from exc
    return json.loads(completed.stdout)


def load_live_queue(repo: str) -> dict[str, Any]:
    prs = _run_gh_json(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(GH_PR_SAFETY_CAP),
            "--json",
            "number,title,url,body,labels,mergeStateStatus",
        ]
    )
    if len(prs) >= GH_PR_SAFETY_CAP:
        raise RuntimeError(
            f"gh pr list reached the {GH_PR_SAFETY_CAP} item safety cap; "
            "use an API-paginated collector before trusting this live report"
        )
    referenced_issues = sorted(
        {ref for pr in prs if isinstance(pr, dict) for ref in _bounty_refs(pr)}
    )
    referenced_issue_numbers = set(referenced_issues)
    issues = _run_gh_json(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            str(GH_ISSUE_SAFETY_CAP),
            "--json",
            "number,title,state,labels",
        ]
    )
    if len(issues) >= GH_ISSUE_SAFETY_CAP:
        raise RuntimeError(
            f"gh issue list reached the {GH_ISSUE_SAFETY_CAP} item safety cap; "
            "use an API-paginated collector before trusting this live report"
        )
    issues_by_number = {
        int(issue["number"]): issue
        for issue in issues
        if isinstance(issue, dict) and isinstance(issue.get("number"), int)
    }
    bounty_numbers = {
        int(issue["number"])
        for issue in issues
        if isinstance(issue, dict)
        and isinstance(issue.get("number"), int)
        and "bounty" in str(issue.get("title", "")).lower()
    } | referenced_issue_numbers
    bounty_issues = []
    for number in sorted(bounty_numbers):
        issue = issues_by_number.get(number, {"number": number})
        viewed_issue = issue
        include_comments = number in referenced_issue_numbers
        if include_comments:
            try:
                viewed_issue = _run_gh_json(
                    [
                        "gh",
                        "issue",
                        "view",
                        str(number),
                        "--repo",
                        repo,
                        "--comments",
                        "--json",
                        "number,title,state,labels,comments",
                    ]
                )
            except RuntimeError:
                if number not in issues_by_number:
                    continue
        bounty_issue = {
            "number": int(viewed_issue["number"]),
            "title": viewed_issue.get("title"),
            "state": viewed_issue.get("state"),
            "labels": viewed_issue.get("labels", []),
            "awards_remaining": 1 if viewed_issue.get("state") == "OPEN" else 0,
        }
        if include_comments:
            bounty_issue["comments"] = viewed_issue.get("comments", [])
        bounty_issues.append(bounty_issue)
    return {"pull_requests": prs, "bounties": bounty_issues}


def _load_input(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("queue input must be a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize MergeWork open PR queue health.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Read queue data from a JSON fixture file.")
    source.add_argument(
        "--repo",
        help="Collect live queue data with gh, for example ramimbo/mergework.",
    )
    parser.add_argument("--format", choices=["json", "markdown", "text"], default="text")
    parser.add_argument("--fail-on-issues", action="store_true")
    args = parser.parse_args(argv)

    data = _load_input(args.input) if args.input else load_live_queue(args.repo)
    report = analyze_queue(data)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.format == "markdown":
        print(format_markdown_report(report))
    else:
        print(format_text_report(report))
    return 1 if args.fail_on_issues and has_queue_issues(report) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
