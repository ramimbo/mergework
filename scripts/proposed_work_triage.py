from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bounty_refs import BOUNTY_REF_RE

GH_TIMEOUT_SECONDS = 30
GH_ISSUE_SAFETY_CAP = 201
PUBLIC_API_LIMIT = 200
DEFAULT_API_HOST = "https://api.mrwk.online"
PROPOSED_WORK_LABEL = "proposed-work"

GITHUB_URL_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/"
    r"(?:issues|pull)/\d+(?:#[A-Za-z0-9_.-]+)?"
)
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)
ROUTED_REF_RE = re.compile(
    r"\b(?:routed|accepted|converted|promoted|reserved|opened|created)\b[^#\n]{0,100}#(\d+)",
    re.IGNORECASE,
)
PROPOSED_TITLE_RE = re.compile(r"^\s*proposed\s+work\s*:\s*", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(
    r"^(?:n/?a|none|no response|todo|tbd|unknown|not sure|\.|-|_no response_)$",
    re.IGNORECASE,
)

SECTION_ALIASES = {
    "problem": "problem",
    "evidence": "evidence",
    "proposed work": "proposed_work",
    "expected value": "expected_value",
    "possible acceptance criteria": "acceptance",
    "acceptance criteria": "acceptance",
    "acceptance": "acceptance",
    "evidence or tests required": "tests_required",
    "tests required": "tests_required",
    "tests": "tests_required",
    "duplicate search": "duplicate_search",
    "related work": "duplicate_search",
    "out of scope": "out_of_scope",
}
REQUIRED_SECTIONS = (
    "problem",
    "evidence",
    "proposed_work",
    "expected_value",
    "acceptance",
    "tests_required",
    "duplicate_search",
    "out_of_scope",
)
SECTION_LABELS = {
    "problem": "Problem",
    "evidence": "Evidence",
    "proposed_work": "Proposed work",
    "expected_value": "Expected value",
    "acceptance": "Possible acceptance criteria",
    "tests_required": "Evidence or tests required",
    "duplicate_search": "Duplicate search",
    "out_of_scope": "Out of scope",
}
WEAK_SECTION_MIN_WORDS = {
    "problem": 4,
    "evidence": 4,
    "proposed_work": 4,
    "expected_value": 4,
    "acceptance": 4,
    "tests_required": 3,
    "duplicate_search": 3,
    "out_of_scope": 3,
}
REJECTED_LABELS = {"duplicate", "invalid", "rejected", "wontfix", "not planned"}
REJECTED_STATE_REASONS = {"not_planned", "duplicate"}
STOPWORDS = {
    "and",
    "for",
    "from",
    "into",
    "live",
    "mrwk",
    "proposed",
    "read",
    "only",
    "report",
    "the",
    "triage",
    "with",
    "work",
}
MUTATING_GH_WORDS = {
    "assign",
    "comment",
    "create",
    "delete",
    "develop",
    "edit",
    "close",
    "label",
    "reopen",
    "merge",
    "mark",
    "review",
    "lock",
    "unlock",
    "pin",
    "unpin",
    "transfer",
    "unassign",
}
MUTATING_GH_API_FIELD_FLAGS = {"-f", "--field", "-F", "--raw-field"}


@dataclass(frozen=True)
class ProposedWorkRow:
    number: int
    title: str
    url: str | None
    state: str
    state_reason: str | None
    labels: list[str]
    author: str
    classification: str
    readiness: str
    missing_sections: list[str]
    weak_sections: list[str]
    warnings: list[str]
    routed_refs: list[int]
    payment_status: str
    payment_url: str | None
    pending_proposal_url: str | None
    suggested_action: str


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _author_login(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        login = raw.get("login")
        if isinstance(login, str) and login:
            return login
    return "unknown"


def _labels(raw: dict[str, Any]) -> list[str]:
    labels = raw.get("labels", [])
    names: list[str] = []
    if not isinstance(labels, list):
        return names
    for label in labels:
        if isinstance(label, str):
            names.append(label)
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            names.append(label["name"])
    return names


def _comments(raw: dict[str, Any]) -> list[dict[str, str]]:
    comments = raw.get("comments", [])
    normalized: list[dict[str, str]] = []
    if not isinstance(comments, list):
        return normalized
    for comment in comments:
        if isinstance(comment, str):
            normalized.append({"body": comment, "url": ""})
        elif isinstance(comment, dict):
            normalized.append(
                {
                    "body": str(comment.get("body") or ""),
                    "url": str(comment.get("url") or ""),
                }
            )
    return normalized


def _state(raw: dict[str, Any]) -> str:
    return str(raw.get("state") or "").lower()


def _state_reason(raw: dict[str, Any]) -> str | None:
    for key in ("stateReason", "state_reason"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    return None


def _normalize_section_title(title: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return SECTION_ALIASES.get(normalized, normalized)


def _section_map(body: str) -> dict[str, str]:
    matches = list(HEADING_RE.finditer(body or ""))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        title = _normalize_section_title(match.group(1))
        if title not in REQUIRED_SECTIONS:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[title] = body[start:end].strip()
    return sections


def _word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9_]+", value or ""))


def _is_weak_section(section: str, content: str) -> bool:
    cleaned = _single_line(content).strip(" -*_`\t\r\n")
    if not cleaned:
        return True
    if PLACEHOLDER_RE.fullmatch(cleaned):
        return True
    return _word_count(cleaned) < WEAK_SECTION_MIN_WORDS[section]


def _looks_like_proposed_work(raw: dict[str, Any], labels: list[str]) -> bool:
    label_set = {label.lower() for label in labels}
    title = str(raw.get("title") or "")
    body = str(raw.get("body") or "")
    return (
        PROPOSED_WORK_LABEL in label_set
        or bool(PROPOSED_TITLE_RE.match(title))
        or "proposed work" in body.lower()
    )


def _issue_text(raw: dict[str, Any]) -> str:
    comment_text = "\n".join(comment["body"] for comment in _comments(raw))
    return "\n".join([str(raw.get("title") or ""), str(raw.get("body") or ""), comment_text])


def _github_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in GITHUB_URL_RE.findall(text or ""):
        url = match.rstrip(".,)")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _issue_surface_urls(raw: dict[str, Any]) -> set[str]:
    surfaces: set[str] = set()
    url = str(raw.get("url") or "").strip()
    if url:
        surfaces.add(url.rstrip(".,)"))
    for comment in _comments(raw):
        if comment["url"]:
            surfaces.add(comment["url"].rstrip(".,)"))
        surfaces.update(_github_urls(comment["body"]))
    surfaces.update(_github_urls(str(raw.get("body") or "")))
    return {surface for surface in surfaces if surface}


def _bounty_refs(text: str) -> list[int]:
    refs: set[int] = set()
    for match in BOUNTY_REF_RE.findall(text or ""):
        try:
            refs.add(int(match))
        except ValueError:
            continue
    for match in ROUTED_REF_RE.findall(text or ""):
        try:
            refs.add(int(match))
        except ValueError:
            continue
    return sorted(refs)


def _is_routed(text: str, labels: list[str]) -> tuple[bool, list[int]]:
    lowered = text.lower()
    refs = _bounty_refs(text)
    label_set = {label.lower() for label in labels}
    routed = (
        bool(refs)
        and any(word in lowered for word in ("routed", "accepted", "converted", "promoted"))
    ) or "reserved on mergework" in lowered
    if PROPOSED_WORK_LABEL in label_set and "mrwk:bounty" in label_set:
        routed = True
    return routed, refs


def _is_rejected(raw: dict[str, Any], labels: list[str], text: str) -> bool:
    label_set = {label.lower() for label in labels}
    if label_set & REJECTED_LABELS:
        return True
    reason = _state_reason(raw)
    if _state(raw) == "closed" and reason in REJECTED_STATE_REASONS:
        return True
    lowered = text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "closed as not planned",
            "maintainer rejected",
            "rejected by maintainer",
            "will not be accepted",
        )
    )


def _source_matches_issue(source: str, surfaces: set[str]) -> bool:
    clean_source = source.rstrip(".,)")
    for surface in surfaces:
        if clean_source == surface or clean_source.startswith(f"{surface}#"):
            return True
    return False


def _proof_sources(data: dict[str, Any], api_host: str) -> dict[str, str]:
    proof_by_source: dict[str, str] = {}
    proof_rows: list[Any] = []
    for key in ("proofs", "accepted_awards", "activity", "recent", "contributors"):
        raw = data.get(key)
        if isinstance(raw, list):
            proof_rows.extend(raw)
        elif isinstance(raw, dict):
            for nested_key in ("contributors", "recent"):
                nested = raw.get(nested_key)
                if isinstance(nested, list):
                    proof_rows.extend(nested)
    for item in proof_rows:
        if not isinstance(item, dict):
            continue
        source = str(
            item.get("source_url")
            or item.get("submission_url")
            or item.get("latest_submission_url")
            or ""
        ).rstrip(".,)")
        proof = str(item.get("proof_url") or item.get("latest_proof_url") or "").strip()
        if proof.startswith("/"):
            proof = f"{api_host.rstrip('/')}{proof}"
        if source and proof:
            proof_by_source[source] = proof
    return proof_by_source


def _pending_sources(data: dict[str, Any], api_host: str) -> dict[str, dict[str, Any]]:
    pending_by_source: dict[str, dict[str, Any]] = {}
    for bounty in data.get("bounties", []):
        if not isinstance(bounty, dict):
            continue
        proposals = bounty.get("pending_payout_proposals", [])
        if not isinstance(proposals, list):
            continue
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            source = str(proposal.get("submission_url") or "").rstrip(".,)")
            if not source:
                continue
            proposal_id = proposal.get("proposal_id", proposal.get("id"))
            proposal_url = str(proposal.get("proposal_url") or "")
            if not proposal_url and proposal_id is not None:
                proposal_url = f"{api_host.rstrip('/')}/api/v1/treasury/proposals/{proposal_id}"
            pending_by_source[source] = {
                "proposal_id": proposal_id,
                "proposal_url": proposal_url or None,
                "executes_after": proposal.get("executes_after"),
            }
    return pending_by_source


def _payment_status(
    raw: dict[str, Any],
    proof_by_source: dict[str, str],
    pending_by_source: dict[str, dict[str, Any]],
) -> tuple[str, str | None, str | None]:
    surfaces = _issue_surface_urls(raw)
    for source, proof in sorted(proof_by_source.items()):
        if _source_matches_issue(source, surfaces):
            return "proof_backed_paid", proof, None
    for source, pending in sorted(pending_by_source.items()):
        if _source_matches_issue(source, surfaces):
            return "pending_payout", None, pending.get("proposal_url")
    return "none", None, None


def _suggested_action(
    classification: str,
    readiness: str,
    labels: list[str],
    payment_status: str,
) -> str:
    if payment_status == "proof_backed_paid":
        return "Do not relabel as pending; keep separate as proof-backed paid work."
    if payment_status == "pending_payout":
        return "Keep separate as accepted pending payout until a public proof exists."
    if classification == "rejected":
        return "Leave closed/rejected unless a maintainer explicitly reopens the scope."
    if classification == "routed":
        return "No duplicate bounty needed; follow the linked live bounty or routed issue."
    if PROPOSED_WORK_LABEL not in {label.lower() for label in labels}:
        return "Add the proposed-work label or ask the author to reopen with the template."
    if readiness == "incomplete":
        return "Ask for missing template evidence before considering a bounty proposal."
    return "Ready for maintainer intake review; still not claimable until a bounty is reserved."


def _row_for_issue(
    raw: dict[str, Any],
    proof_by_source: dict[str, str],
    pending_by_source: dict[str, dict[str, Any]],
) -> ProposedWorkRow | None:
    labels = _labels(raw)
    if not _looks_like_proposed_work(raw, labels):
        return None
    body = str(raw.get("body") or "")
    sections = _section_map(body)
    missing = [section for section in REQUIRED_SECTIONS if section not in sections]
    weak = [
        section
        for section in REQUIRED_SECTIONS
        if section in sections and _is_weak_section(section, sections[section])
    ]
    text = _issue_text(raw)
    routed, refs = _is_routed(text, labels)
    rejected = _is_rejected(raw, labels, text)
    if rejected:
        classification = "rejected"
    elif routed:
        classification = "routed"
    else:
        classification = "active"
    warnings: list[str] = []
    if PROPOSED_WORK_LABEL not in {label.lower() for label in labels}:
        warnings.append("missing proposed-work label")
    if _word_count(body) < 35:
        warnings.append("body is short for maintainer intake")
    readiness = "complete" if not missing and not weak else "incomplete"
    payment_status, payment_url, pending_url = _payment_status(
        raw, proof_by_source, pending_by_source
    )
    return ProposedWorkRow(
        number=int(raw["number"]),
        title=str(raw.get("title") or ""),
        url=str(raw.get("url") or "") or None,
        state=_state(raw),
        state_reason=_state_reason(raw),
        labels=labels,
        author=_author_login(raw.get("author")),
        classification=classification,
        readiness=readiness,
        missing_sections=[SECTION_LABELS[section] for section in missing],
        weak_sections=[SECTION_LABELS[section] for section in weak],
        warnings=warnings,
        routed_refs=refs,
        payment_status=payment_status,
        payment_url=payment_url,
        pending_proposal_url=pending_url,
        suggested_action=_suggested_action(classification, readiness, labels, payment_status),
    )


def _scope_tokens(title: str) -> set[str]:
    clean = PROPOSED_TITLE_RE.sub("", title or "")
    words = re.findall(r"[a-z0-9]+", clean.lower())
    return {word for word in words if len(word) >= 3 and word not in STOPWORDS}


def _related_groups(rows: list[ProposedWorkRow]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for row in rows:
        if row.classification == "rejected":
            continue
        tokens = _scope_tokens(row.title)
        if len(tokens) < 2:
            continue
        matched_cluster: dict[str, Any] | None = None
        for cluster in clusters:
            cluster_tokens: set[str] = cluster["tokens"]
            common = tokens & cluster_tokens
            union = tokens | cluster_tokens
            if len(common) >= 2 and len(common) / len(union) >= 0.45:
                matched_cluster = cluster
                break
        if matched_cluster is None:
            clusters.append({"tokens": set(tokens), "rows": [row]})
        else:
            matched_cluster["tokens"] |= tokens
            matched_cluster["rows"].append(row)
    groups: list[dict[str, Any]] = []
    for cluster in clusters:
        cluster_rows: list[ProposedWorkRow] = cluster["rows"]
        if len(cluster_rows) < 2:
            continue
        common = set.intersection(*[_scope_tokens(row.title) for row in cluster_rows])
        scope_words = sorted(common or cluster["tokens"])
        groups.append(
            {
                "scope": " ".join(scope_words),
                "issues": [
                    {"number": row.number, "title": row.title, "url": row.url}
                    for row in sorted(cluster_rows, key=lambda item: item.number)
                ],
                "suggested_consolidation": (
                    "Consider one maintainer decision/bounty covering "
                    f"{', '.join(scope_words[:6]) or 'the shared scope'}."
                ),
            }
        )
    return groups


def analyze_proposed_work(
    data: dict[str, Any], *, api_host: str = DEFAULT_API_HOST
) -> dict[str, Any]:
    proof_by_source = _proof_sources(data, api_host)
    pending_by_source = _pending_sources(data, api_host)
    rows: list[ProposedWorkRow] = []
    for issue in data.get("issues", []):
        if not isinstance(issue, dict) or not isinstance(issue.get("number"), int):
            continue
        row = _row_for_issue(issue, proof_by_source, pending_by_source)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda item: item.number)
    related = _related_groups(rows)
    row_dicts = [asdict(row) for row in rows]

    def count_where(key: str, value: str) -> int:
        return sum(1 for row in row_dicts if row[key] == value)

    report = {
        "summary": {
            "issues_scanned": len(
                [item for item in data.get("issues", []) if isinstance(item, dict)]
            ),
            "proposed_work_issues": len(row_dicts),
            "active": count_where("classification", "active"),
            "routed": count_where("classification", "routed"),
            "rejected": count_where("classification", "rejected"),
            "complete": count_where("readiness", "complete"),
            "incomplete": count_where("readiness", "incomplete"),
            "label_missing": sum(
                1 for row in row_dicts if "missing proposed-work label" in row["warnings"]
            ),
            "proof_backed_paid": count_where("payment_status", "proof_backed_paid"),
            "pending_payout": count_where("payment_status", "pending_payout"),
            "related_groups": len(related),
        },
        "issues": row_dicts,
        "complete": [row for row in row_dicts if row["readiness"] == "complete"],
        "incomplete": [row for row in row_dicts if row["readiness"] == "incomplete"],
        "missing_label": [
            row for row in row_dicts if "missing proposed-work label" in row["warnings"]
        ],
        "routed": [row for row in row_dicts if row["classification"] == "routed"],
        "rejected": [row for row in row_dicts if row["classification"] == "rejected"],
        "proof_backed_paid": [
            row for row in row_dicts if row["payment_status"] == "proof_backed_paid"
        ],
        "pending_payout": [row for row in row_dicts if row["payment_status"] == "pending_payout"],
        "related_groups": related,
    }
    return report


def has_triage_warnings(report: dict[str, Any]) -> bool:
    summary = report["summary"]
    return any(
        int(summary[key]) > 0
        for key in ("incomplete", "label_missing", "rejected", "related_groups")
    )


def _markdown_issue_link(row: dict[str, Any]) -> str:
    label = f"#{row['number']}"
    if row.get("url"):
        return f"[{label}]({row['url']})"
    return label


def _row_detail(row: dict[str, Any]) -> str:
    details: list[str] = []
    if row.get("missing_sections"):
        details.append("missing: " + ", ".join(row["missing_sections"]))
    if row.get("weak_sections"):
        details.append("weak: " + ", ".join(row["weak_sections"]))
    if row.get("warnings"):
        details.append("warnings: " + ", ".join(row["warnings"]))
    if row.get("routed_refs"):
        details.append("refs: " + ", ".join(f"#{ref}" for ref in row["routed_refs"]))
    if row.get("payment_url"):
        details.append(f"proof: {row['payment_url']}")
    if row.get("pending_proposal_url"):
        details.append(f"pending: {row['pending_proposal_url']}")
    return "; ".join(details) or "no issues found"


def format_markdown_report(report: dict[str, Any]) -> str:
    lines = ["## Proposed Work Intake Triage", ""]
    for key, value in report["summary"].items():
        lines.append(f"- **{key.replace('_', ' ')}**: {value}")
    lines.append("")
    lines.append("| Issue | State | Status | Payment | Action |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in report["issues"]:
        status = f"{row['classification']} / {row['readiness']}"
        payment = row["payment_status"]
        lines.append(
            f"| {_markdown_issue_link(row)} | `{row['state'] or 'unknown'}` | "
            f"`{status}` | `{payment}` | {_single_line(row['suggested_action'])} |"
        )
        detail = _row_detail(row)
        if detail != "no issues found":
            lines.append(f"|  |  |  |  | {_single_line(detail)} |")
    if report["related_groups"]:
        lines.append("")
        lines.append("### Likely related or duplicate scopes")
        for group in report["related_groups"]:
            issue_refs = ", ".join(
                f"[#{issue['number']}]({issue['url']})"
                if issue.get("url")
                else f"#{issue['number']}"
                for issue in group["issues"]
            )
            lines.append(
                f"- **{_single_line(group['scope'])}**: {issue_refs}. "
                f"{_single_line(group['suggested_consolidation'])}"
            )
    return "\n".join(lines)


def format_text_report(report: dict[str, Any]) -> str:
    lines = ["Proposed Work Intake Triage"]
    for key, value in report["summary"].items():
        lines.append(f"- {key.replace('_', ' ')}: {value}")
    for row in report["issues"]:
        lines.append(
            f"- #{row['number']} {row['title']}: {row['classification']} / "
            f"{row['readiness']} / {row['payment_status']}"
        )
        detail = _row_detail(row)
        if detail != "no issues found":
            lines.append(f"  - {detail}")
        lines.append(f"  - action: {row['suggested_action']}")
    if report["related_groups"]:
        lines.append("Likely related or duplicate scopes")
        for group in report["related_groups"]:
            issues = ", ".join(f"#{issue['number']}" for issue in group["issues"])
            lines.append(f"- {group['scope']}: {issues}")
    return "\n".join(lines)


def _assert_read_only_gh(args: list[str]) -> None:
    if not args or args[0] != "gh":
        raise RuntimeError(f"expected gh command, got: {' '.join(args)}")
    if any(word in MUTATING_GH_WORDS for word in args):
        raise RuntimeError(f"refusing non-read-only gh command: {' '.join(args)}")
    if args[:2] == ["gh", "api"]:
        if any(arg in MUTATING_GH_API_FIELD_FLAGS for arg in args):
            raise RuntimeError(f"refusing gh api field mutation flags: {' '.join(args)}")
        for flag in ("--method", "-X"):
            if flag in args:
                index = args.index(flag)
                if index + 1 >= len(args) or args[index + 1].upper() not in {"GET", "HEAD"}:
                    raise RuntimeError(f"refusing non-read-only gh api command: {' '.join(args)}")


def _run_gh_json(args: list[str]) -> Any:
    _assert_read_only_gh(args)
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


def _get_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "mergework-proposed-work-triage",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=GH_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (TimeoutError, urllib.error.URLError) as exc:
        raise RuntimeError(f"public API request failed: {url}") from exc


def load_public_payment_state(api_host: str = DEFAULT_API_HOST) -> dict[str, Any]:
    host = api_host.rstrip("/")
    bounties = _get_json(f"{host}/api/v1/bounties?limit={PUBLIC_API_LIMIT}")
    activity = _get_json(f"{host}/api/v1/activity?limit={PUBLIC_API_LIMIT}")
    if not isinstance(bounties, list):
        raise RuntimeError("unexpected /api/v1/bounties response shape: expected list")
    if not isinstance(activity, dict):
        raise RuntimeError("unexpected /api/v1/activity response shape: expected object")
    data: dict[str, Any] = {"bounties": bounties}
    for key in ("contributors", "recent"):
        value = activity.get(key)
        if isinstance(value, list):
            data[key] = value
    return data


def load_live_triage(
    repo: str,
    *,
    api_host: str = DEFAULT_API_HOST,
    include_public_api: bool = True,
) -> dict[str, Any]:
    candidate_issues: dict[int, dict[str, Any]] = {}
    list_commands = [
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--label",
            PROPOSED_WORK_LABEL,
            "--limit",
            str(GH_ISSUE_SAFETY_CAP),
            "--json",
            "number,title,url,state,stateReason,labels,author",
        ],
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--search",
            '"Proposed work" in:title',
            "--limit",
            str(GH_ISSUE_SAFETY_CAP),
            "--json",
            "number,title,url,state,stateReason,labels,author",
        ],
    ]
    for command in list_commands:
        issues = _run_gh_json(command)
        if len(issues) >= GH_ISSUE_SAFETY_CAP:
            raise RuntimeError(
                f"gh issue list reached the {GH_ISSUE_SAFETY_CAP} item safety cap; "
                "use an API-paginated collector before trusting this live report"
            )
        for issue in issues:
            if isinstance(issue, dict) and isinstance(issue.get("number"), int):
                candidate_issues[int(issue["number"])] = issue
    detailed: list[dict[str, Any]] = []
    for number in sorted(candidate_issues):
        detailed.append(
            _run_gh_json(
                [
                    "gh",
                    "issue",
                    "view",
                    str(number),
                    "--repo",
                    repo,
                    "--comments",
                    "--json",
                    "number,title,url,body,state,stateReason,labels,author,comments",
                ]
            )
        )
    data: dict[str, Any] = {"issues": detailed}
    if include_public_api:
        data.update(load_public_payment_state(api_host))
    return data


def _load_input(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("proposed-work triage input must be a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize MergeWork proposed-work intake without mutating GitHub."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Read proposed-work data from a JSON fixture file.")
    source.add_argument(
        "--repo",
        help="Collect live proposed-work data with read-only gh commands, e.g. ramimbo/mergework.",
    )
    parser.add_argument("--format", choices=["json", "markdown", "text"], default="text")
    parser.add_argument("--api-host", default=DEFAULT_API_HOST)
    parser.add_argument(
        "--no-public-api",
        action="store_true",
        help=(
            "Skip read-only MergeWork public API reads. Live mode normally uses "
            "public bounties/activity to classify #649 paid and pending intake."
        ),
    )
    parser.add_argument("--fail-on-warnings", action="store_true")
    args = parser.parse_args(argv)

    data = (
        _load_input(args.input)
        if args.input
        else load_live_triage(
            args.repo,
            api_host=args.api_host,
            include_public_api=not args.no_public_api,
        )
    )
    report = analyze_proposed_work(data, api_host=args.api_host)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.format == "markdown":
        print(format_markdown_report(report))
    else:
        print(format_text_report(report))
    return 1 if args.fail_on_warnings and has_triage_warnings(report) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
