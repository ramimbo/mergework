from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_API_HOST = "https://api.mrwk.online"
GH_TIMEOUT_SECONDS = 30
GH_LIMIT = 200
PROPOSED_WORK_LABEL = "proposed-work"
INTAKE_BOUNTY_ISSUE = 649
GITHUB_URL_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/"
    r"(?:issues|pull)/\d+(?:#[A-Za-z0-9_.-]+)?"
)
ISSUE_REF_RE = re.compile(r"(?<![A-Za-z0-9_])#(\d+)\b")
SECTION_ALIASES = {
    "problem": ("Problem",),
    "evidence": ("Evidence",),
    "proposed_work": ("Proposed work",),
    "expected_value": ("Expected value",),
    "acceptance": ("Possible acceptance criteria", "Acceptance criteria"),
    "tests": ("Evidence or tests required", "Tests required"),
    "duplicate_search": ("Duplicate search",),
    "out_of_scope": ("Out of scope",),
}
REQUIRED_SECTIONS = tuple(SECTION_ALIASES)
VAGUE_MARKERS = {"", "n/a", "na", "none", "tbd", "todo", "unknown"}
STOPWORDS = {
    "a",
    "add",
    "and",
    "api",
    "for",
    "from",
    "in",
    "issue",
    "mrwk",
    "of",
    "on",
    "proposed",
    "public",
    "the",
    "to",
    "work",
}


@dataclass(frozen=True)
class ProposedWorkRow:
    issue: int
    title: str
    url: str
    author: str
    labels: list[str]
    missing_sections: list[str]
    warnings: list[str]
    intake_status: str
    intake_proof_url: str | None = None
    pending_proposal_url: str | None = None
    pending_executes_after: str | None = None
    related_group: str | None = None


def _normalize_url(url: str) -> str:
    return str(url or "").strip().rstrip(".,)")


def _labels(raw: dict[str, Any]) -> list[str]:
    labels = raw.get("labels", [])
    if not isinstance(labels, list):
        return []
    names: list[str] = []
    for label in labels:
        if isinstance(label, str):
            names.append(label)
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            names.append(label["name"])
    return names


def _login(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("login"), str):
        return raw["login"]
    return "unknown"


def _text(raw: Any) -> str:
    return str(raw or "")


def _squash(text: str) -> str:
    return " ".join(text.split())


def _body_with_comments(issue: dict[str, Any]) -> str:
    parts = [_text(issue.get("title")), _text(issue.get("body"))]
    comments = issue.get("comments", [])
    if isinstance(comments, list):
        for comment in comments:
            if isinstance(comment, dict):
                parts.append(_text(comment.get("body")))
    return "\n".join(parts)


def _heading_pattern(label: str) -> re.Pattern[str]:
    escaped = re.escape(label).replace(r"\ ", r"\s+")
    return re.compile(rf"(?im)^\s*#+\s*{escaped}\s*$")


def _section_content(body: str, aliases: tuple[str, ...]) -> str:
    matches: list[re.Match[str]] = []
    for alias in aliases:
        matches.extend(_heading_pattern(alias).finditer(body))
    if not matches:
        return ""
    match = min(matches, key=lambda item: item.start())
    tail = body[match.end() :]
    next_heading = re.search(r"(?m)^\s*#+\s+", tail)
    return tail[: next_heading.start()] if next_heading else tail


def _missing_sections(body: str) -> list[str]:
    missing: list[str] = []
    for key, aliases in SECTION_ALIASES.items():
        if not _section_content(body, aliases).strip():
            missing.append(key)
    return missing


def _vague_sections(body: str) -> list[str]:
    vague: list[str] = []
    for key in ("problem", "evidence", "proposed_work", "expected_value"):
        content = _squash(_section_content(body, SECTION_ALIASES[key])).lower()
        words = re.findall(r"[a-z0-9]+", content)
        if content in VAGUE_MARKERS or len(words) < 4:
            vague.append(key)
    return vague


def _issue_like_proposed_work(issue: dict[str, Any]) -> bool:
    title = _text(issue.get("title")).lower()
    labels = {label.lower() for label in _labels(issue)}
    return PROPOSED_WORK_LABEL in labels or title.startswith("proposed work:")


def _github_urls(text: str) -> set[str]:
    return {_normalize_url(match) for match in GITHUB_URL_RE.findall(text or "")}


def _issue_refs(text: str) -> set[int]:
    refs: set[int] = set()
    for match in ISSUE_REF_RE.findall(text or ""):
        try:
            refs.add(int(match))
        except ValueError:
            continue
    return refs


def _title_tokens(title: str) -> set[str]:
    cleaned = re.sub(r"^\s*proposed\s+work\s*:\s*", "", title, flags=re.IGNORECASE)
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", cleaned.lower())
        if len(token) > 2 and token not in STOPWORDS
    }
    return tokens


def _related_key(issue: dict[str, Any]) -> tuple[str, str] | None:
    text = _body_with_comments(issue)
    urls = sorted(_github_urls(text))
    if urls:
        return ("url", urls[0])
    refs = sorted(ref for ref in _issue_refs(text) if ref != issue.get("number"))
    if refs:
        return ("ref", str(refs[0]))
    tokens = sorted(_title_tokens(_text(issue.get("title"))))
    if len(tokens) >= 2:
        return ("tokens", " ".join(tokens[:4]))
    return None


def _related_groups(issues: list[dict[str, Any]]) -> dict[int, str]:
    keyed: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for issue in issues:
        key = _related_key(issue)
        if key is not None:
            keyed[key].append(issue)
    group_by_issue: dict[int, str] = {}
    group_index = 1
    for _key, members in sorted(keyed.items(), key=lambda item: item[0]):
        if len(members) < 2:
            continue
        group_id = f"group-{group_index}"
        group_index += 1
        for issue in members:
            number = issue.get("number")
            if isinstance(number, int):
                group_by_issue[number] = group_id
    return group_by_issue


def _paid_sources(data: dict[str, Any], api_host: str) -> dict[str, str]:
    paid: dict[str, str] = {}
    rows: list[Any] = []
    activity = data.get("activity")
    if isinstance(activity, dict):
        recent = activity.get("recent")
        if isinstance(recent, list):
            rows.extend(recent)
    recent = data.get("recent")
    if isinstance(recent, list):
        rows.extend(recent)
    for item in rows:
        if not isinstance(item, dict):
            continue
        issue_number = item.get("bounty_issue_number")
        if issue_number != INTAKE_BOUNTY_ISSUE:
            continue
        source = _normalize_url(_text(item.get("submission_url") or item.get("source_url")))
        proof = _text(item.get("proof_url") or item.get("latest_proof_url"))
        if not source or not proof:
            continue
        paid[source] = proof if proof.startswith("http") else f"{api_host.rstrip('/')}{proof}"
    return paid


def _pending_sources(data: dict[str, Any], api_host: str) -> dict[str, dict[str, str | None]]:
    pending: dict[str, dict[str, str | None]] = {}
    for bounty in data.get("bounties", []):
        if not isinstance(bounty, dict):
            continue
        issue_number = bounty.get("issue_number", bounty.get("number"))
        if issue_number != INTAKE_BOUNTY_ISSUE:
            continue
        proposals = bounty.get("pending_payout_proposals", [])
        if not isinstance(proposals, list):
            continue
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            source = _normalize_url(_text(proposal.get("submission_url")))
            if not source:
                continue
            proposal_id = proposal.get("proposal_id", proposal.get("id"))
            proposal_url = None
            if proposal_id is not None:
                proposal_url = f"{api_host.rstrip('/')}/api/v1/treasury/proposals/{proposal_id}"
            pending[source] = {
                "proposal_url": proposal_url,
                "executes_after": _text(proposal.get("executes_after")) or None,
            }
    return pending


def _intake_status(
    issue: dict[str, Any],
    *,
    paid_sources: dict[str, str],
    pending_sources: dict[str, dict[str, str | None]],
) -> tuple[str, str | None, str | None, str | None]:
    url = _normalize_url(_text(issue.get("url")))
    if url in paid_sources:
        return "paid", paid_sources[url], None, None
    if url in pending_sources:
        pending = pending_sources[url]
        return "pending_payout", None, pending.get("proposal_url"), pending.get("executes_after")
    return "not_found", None, None, None


def _is_rejected(issue: dict[str, Any], text: str) -> bool:
    labels = {label.lower() for label in _labels(issue)}
    if labels & {"mrwk:rejected", "wontfix", "not-planned"}:
        return True
    return bool(
        re.search(
            r"\b(maintainer\s+decision:\s*)?(rejected|declined|not\s+planned)\b",
            text,
            re.I,
        )
    )


def _is_routed(text: str, intake_status: str) -> bool:
    if intake_status in {"paid", "pending_payout"}:
        return True
    return bool(
        re.search(
            r"\b(already\s+routed|separate\s+implementation|created\s+bounty|"
            r"follow-up\s+pr|routed\s+to)\b",
            text,
            re.I,
        )
    )


def _has_non_live_confusion(issue: dict[str, Any], text: str) -> bool:
    labels = {label.lower() for label in _labels(issue)}
    if "mrwk:bounty" in labels:
        return False
    lowered = text.lower()
    safe_claim_disclaimer = "do not submit `/claim`" in lowered or "do not submit /claim" in lowered
    if "/claim" in lowered and not safe_claim_disclaimer:
        return True
    if "reserved on mergework" in lowered:
        return True
    if "claimable" in lowered and "not claimable" not in lowered:
        return True
    return "live bounty" in lowered and "not a live" not in lowered


def _row_for_issue(
    issue: dict[str, Any],
    *,
    group_by_issue: dict[int, str],
    duplicate_group_counts: Counter[str],
    paid_sources: dict[str, str],
    pending_sources: dict[str, dict[str, str | None]],
) -> ProposedWorkRow:
    body = _text(issue.get("body"))
    all_text = _body_with_comments(issue)
    labels = _labels(issue)
    normalized_labels = {label.lower() for label in labels}
    missing_sections = _missing_sections(body)
    vague_sections = _vague_sections(body)
    intake_status, proof_url, proposal_url, executes_after = _intake_status(
        issue,
        paid_sources=paid_sources,
        pending_sources=pending_sources,
    )
    number = issue.get("number")
    group_id = group_by_issue.get(number) if isinstance(number, int) else None
    related_key = _related_key(issue)
    warnings: list[str] = []
    if PROPOSED_WORK_LABEL not in normalized_labels:
        warnings.append("label_missing")
    if missing_sections:
        warnings.append("missing_template_sections")
    if vague_sections:
        warnings.append("vague")
    if group_id and related_key is not None and duplicate_group_counts[group_id] > 1:
        warnings.append("duplicate_looking")
    if _is_routed(all_text, intake_status):
        warnings.append("already_routed")
    if _is_rejected(issue, all_text):
        warnings.append("rejected")
    if "mrwk:needs-info" in normalized_labels:
        warnings.append("needs_info")
    if _has_non_live_confusion(issue, all_text):
        warnings.append("non_live_confused")
    return ProposedWorkRow(
        issue=int(number) if isinstance(number, int) else 0,
        title=_text(issue.get("title")),
        url=_normalize_url(_text(issue.get("url"))),
        author=_login(issue.get("author")),
        labels=labels,
        missing_sections=missing_sections,
        warnings=sorted(set(warnings)),
        intake_status=intake_status,
        intake_proof_url=proof_url,
        pending_proposal_url=proposal_url,
        pending_executes_after=executes_after,
        related_group=group_id,
    )


def _suggested_scope(group_id: str, members: list[ProposedWorkRow]) -> dict[str, Any]:
    token_counts: Counter[str] = Counter()
    for member in members:
        token_counts.update(_title_tokens(member.title))
    terms = [term for term, _count in token_counts.most_common(5)]
    scope = " ".join(terms) if terms else "related proposed work"
    return {
        "group": group_id,
        "issues": [member.issue for member in members],
        "evidence": [member.url for member in members],
        "suggested_scope": f"Consolidate related proposed-work requests around {scope}.",
    }


def analyze_proposed_work(
    data: dict[str, Any], *, api_host: str = DEFAULT_API_HOST
) -> dict[str, Any]:
    issues = [
        issue
        for issue in data.get("issues", [])
        if isinstance(issue, dict) and _issue_like_proposed_work(issue)
    ]
    group_by_issue = _related_groups(issues)
    group_counts = Counter(group_by_issue.values())
    paid = _paid_sources(data, api_host)
    pending = _pending_sources(data, api_host)
    rows = [
        _row_for_issue(
            issue,
            group_by_issue=group_by_issue,
            duplicate_group_counts=group_counts,
            paid_sources=paid,
            pending_sources=pending,
        )
        for issue in issues
    ]
    rows.sort(key=lambda row: row.issue)
    grouped_rows: dict[str, list[ProposedWorkRow]] = defaultdict(list)
    for row in rows:
        if row.related_group:
            grouped_rows[row.related_group].append(row)
    suggestions = [
        _suggested_scope(group_id, members)
        for group_id, members in sorted(grouped_rows.items())
        if len(members) > 1
    ]
    warning_counts = Counter(warning for row in rows for warning in row.warnings)
    intake_counts = Counter(row.intake_status for row in rows)
    return {
        "summary": {
            "proposed_work_issues": len(rows),
            "paid_intake": intake_counts["paid"],
            "pending_intake": intake_counts["pending_payout"],
            "missing_label": warning_counts["label_missing"],
            "missing_template_sections": warning_counts["missing_template_sections"],
            "duplicate_looking": warning_counts["duplicate_looking"],
            "already_routed": warning_counts["already_routed"],
            "rejected": warning_counts["rejected"],
            "needs_info": warning_counts["needs_info"],
            "non_live_confused": warning_counts["non_live_confused"],
            "suggested_consolidations": len(suggestions),
        },
        "warning_enum": [
            "label_missing",
            "missing_template_sections",
            "vague",
            "duplicate_looking",
            "already_routed",
            "rejected",
            "needs_info",
            "non_live_confused",
        ],
        "rows": [asdict(row) for row in rows],
        "suggested_bounty_scopes": suggestions,
    }


def _markdown_link(label: str, url: str | None) -> str:
    return f"[{label}]({url})" if url else label


def format_markdown_report(report: dict[str, Any]) -> str:
    lines = ["## Proposed Work Intake Triage", ""]
    for key, value in report["summary"].items():
        lines.append(f"- **{key.replace('_', ' ')}**: {value}")
    lines.extend(
        [
            "",
            "| Issue | Intake | Warnings | Related |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in report["rows"]:
        issue = _markdown_link(f"#{row['issue']}", row.get("url"))
        intake = row["intake_status"]
        if row.get("intake_proof_url"):
            intake = _markdown_link("paid", row["intake_proof_url"])
        elif row.get("pending_proposal_url"):
            intake = _markdown_link("pending payout", row["pending_proposal_url"])
        warnings = ", ".join(f"`{warning}`" for warning in row["warnings"]) or "-"
        related = row.get("related_group") or "-"
        lines.append(f"| {issue} | {intake} | {warnings} | {related} |")
    if report["suggested_bounty_scopes"]:
        lines.extend(["", "### Suggested Consolidations", ""])
        for suggestion in report["suggested_bounty_scopes"]:
            issues = ", ".join(f"#{issue}" for issue in suggestion["issues"])
            lines.append(f"- `{suggestion['group']}` ({issues}): {suggestion['suggested_scope']}")
    return "\n".join(lines)


def _run_gh_json(args: list[str]) -> Any:
    if args[:2] == ["gh", "api"]:
        for flag in ("--method", "-X"):
            if flag not in args:
                continue
            index = args.index(flag)
            if index + 1 >= len(args) or args[index + 1].upper() not in {"GET", "HEAD"}:
                raise RuntimeError(f"refusing non-read-only gh api command: {' '.join(args)}")
    if any(arg in {"comment", "edit", "close", "reopen", "merge", "review"} for arg in args):
        raise RuntimeError(f"refusing non-read-only gh command: {' '.join(args)}")
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
        raise RuntimeError(f"gh command timed out after {GH_TIMEOUT_SECONDS}s") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"gh command failed with exit {exc.returncode}: {' '.join(args)}\n{exc.stderr}"
        ) from exc
    return json.loads(completed.stdout)


def _get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=GH_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"public API request failed: {url}") from exc


def load_public_api_state(api_host: str) -> dict[str, Any]:
    host = api_host.rstrip("/")
    bounties = _get_json(f"{host}/api/v1/bounties?limit={GH_LIMIT}")
    activity = _get_json(f"{host}/api/v1/activity?limit={GH_LIMIT}")
    state: dict[str, Any] = {}
    if isinstance(bounties, list):
        state["bounties"] = bounties
    if isinstance(activity, dict):
        state["activity"] = activity
    return state


def load_live_triage(repo: str, api_host: str) -> dict[str, Any]:
    issue_list = _run_gh_json(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(GH_LIMIT),
            "--json",
            "number,title,url,labels,author",
        ]
    )
    issues: list[dict[str, Any]] = []
    for issue in issue_list:
        if not isinstance(issue, dict) or not _issue_like_proposed_work(issue):
            continue
        issue_number = issue.get("number")
        if not isinstance(issue_number, int):
            continue
        issue_view = _run_gh_json(
            [
                "gh",
                "issue",
                "view",
                str(issue_number),
                "--repo",
                repo,
                "--json",
                "number,title,url,body,labels,author,comments",
            ]
        )
        if isinstance(issue_view, dict):
            issues.append(issue_view)
    public_state = load_public_api_state(api_host)
    public_state["issues"] = issues
    return public_state


def _load_input(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("proposed-work triage input must be a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only proposed-work intake triage report for MergeWork maintainers."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Read proposed-work fixture JSON.")
    source.add_argument("--repo", help="Collect live public state with read-only gh calls.")
    parser.add_argument("--api-host", default=DEFAULT_API_HOST)
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = parser.parse_args(argv)

    data = _load_input(args.input) if args.input else load_live_triage(args.repo, args.api_host)
    report = analyze_proposed_work(data, api_host=args.api_host)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_markdown_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
