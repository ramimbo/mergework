from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bounty_refs import BOUNTY_REF_RE

DEFAULT_API_HOST = "https://api.mrwk.ltclab.site"
GH_TIMEOUT_SECONDS = 30
GH_LIMIT = 200
GITHUB_URL_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/"
    r"(?:issues|pull)/\d+(?:#[A-Za-z0-9_.-]+)?"
)
CLAIM_WORD_RE = re.compile(
    r"(^|\s)(/claim|/attempt|claim(?:ing)?|reviewed|verification|smoke[- ]check|"
    r"accepted|paid|proof)(\b|\s|:)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClaimRow:
    source_url: str
    bounty_issue: int | None
    bounty_id: int | None
    claimant: str
    source_type: str
    duplicate_key: str
    likely_status: str
    proof_url: str | None = None


def _author_login(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        login = raw.get("login")
        if isinstance(login, str) and login:
            return login
    return "unknown"


def _label_names(raw: dict[str, Any]) -> list[str]:
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


def _normalize_url(url: str) -> str:
    clean = str(url or "").strip()
    if not clean:
        return ""
    return clean.rstrip(".,)")


def _github_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in GITHUB_URL_RE.findall(text or ""):
        url = _normalize_url(match)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _bounty_refs(text: str) -> list[int]:
    refs: set[int] = set()
    for match in BOUNTY_REF_RE.findall(text or ""):
        try:
            refs.add(int(match))
        except ValueError:
            continue
    return sorted(refs)


def _is_bounty_issue(issue: dict[str, Any]) -> bool:
    title = str(issue.get("title") or "").lower()
    labels = {label.lower() for label in _label_names(issue)}
    return "mrwk:bounty" in labels or "bounty" in title


def _is_candidate(text: str, *, parent_is_bounty: bool = False) -> bool:
    if not text:
        return parent_is_bounty
    return bool(CLAIM_WORD_RE.search(text) or _bounty_refs(text) or _github_urls(text))


def _first_bounty_ref(text: str, fallback: int | None) -> int | None:
    refs = _bounty_refs(text)
    return refs[0] if refs else fallback


def _status_value(raw: dict[str, Any]) -> str:
    return str(raw.get("status") or raw.get("state") or "").lower()


def _bounty_index(data: dict[str, Any]) -> dict[int, dict[str, Any]]:
    index: dict[int, dict[str, Any]] = {}
    for item in data.get("bounties", []):
        if not isinstance(item, dict):
            continue
        issue = item.get("issue_number")
        if issue is None:
            issue = item.get("number")
        if not isinstance(issue, str | int):
            continue
        try:
            issue_number = int(issue)
        except (TypeError, ValueError):
            continue
        bounty_id = item.get("id", item.get("bounty_id"))
        normalized = dict(item)
        try:
            normalized["bounty_id"] = int(bounty_id) if bounty_id is not None else None
        except (TypeError, ValueError):
            normalized["bounty_id"] = None
        index[issue_number] = normalized
    return index


def _proof_sources(data: dict[str, Any], api_host: str) -> dict[str, str]:
    proof_by_source: dict[str, str] = {}
    proof_rows: list[Any] = []
    for key in ("proofs", "accepted_awards", "activity", "recent"):
        raw = data.get(key)
        if isinstance(raw, list):
            proof_rows.extend(raw)
        elif isinstance(raw, dict):
            for nested_key in ("contributors", "recent"):
                nested = raw.get(nested_key)
                if isinstance(nested, list):
                    proof_rows.extend(nested)
    contributors = data.get("contributors")
    if isinstance(contributors, list):
        proof_rows.extend(contributors)
    for item in proof_rows:
        if not isinstance(item, dict):
            continue
        source = _normalize_url(
            str(
                item.get("source_url")
                or item.get("submission_url")
                or item.get("latest_submission_url")
                or ""
            )
        )
        proof = str(item.get("proof_url") or item.get("latest_proof_url") or "")
        if not source or not proof:
            continue
        if proof.startswith("/"):
            proof = f"{api_host.rstrip('/')}{proof}"
        proof_by_source[source] = proof
    return proof_by_source


def _proof_for_surface(text: str, source_url: str, proof_by_source: dict[str, str]) -> str | None:
    proof_url = proof_by_source.get(source_url)
    if proof_url:
        return proof_url
    for linked_url in _github_urls(text):
        proof_url = proof_by_source.get(linked_url)
        if proof_url:
            return proof_url
    return None


def _duplicate_key(text: str, source_url: str, bounty_issue: int | None) -> str:
    linked_urls = [url for url in _github_urls(text) if url != source_url]
    core = linked_urls[0] if linked_urls else source_url
    return f"{bounty_issue or 'unknown'}:{core}"


def _surface_row(
    *,
    text: str,
    source_url: str,
    claimant: str,
    source_type: str,
    fallback_bounty_issue: int | None,
    bounties: dict[int, dict[str, Any]],
    proof_by_source: dict[str, str],
    duplicate_counts: Counter[str],
) -> ClaimRow | None:
    source_url = _normalize_url(source_url)
    parent_is_bounty = fallback_bounty_issue is not None
    if not source_url or not _is_candidate(text, parent_is_bounty=parent_is_bounty):
        return None
    bounty_issue = _first_bounty_ref(text, fallback_bounty_issue)
    duplicate_key = _duplicate_key(text, source_url, bounty_issue)
    proof_url = _proof_for_surface(text, source_url, proof_by_source)
    bounty = bounties.get(bounty_issue) if bounty_issue is not None else None
    bounty_id = bounty.get("bounty_id") if bounty else None
    if proof_url:
        likely_status = "already_paid"
    elif bounty_issue is None:
        likely_status = "missing_bounty_ref"
    elif bounty is None:
        likely_status = "unknown_bounty"
    elif duplicate_counts[duplicate_key] > 1:
        likely_status = "duplicate_candidate"
    elif not CLAIM_WORD_RE.search(text):
        likely_status = "ignored_or_unclear"
    else:
        likely_status = "unpaid_candidate"
    return ClaimRow(
        source_url=source_url,
        bounty_issue=bounty_issue,
        bounty_id=bounty_id,
        claimant=claimant,
        source_type=source_type,
        duplicate_key=duplicate_key,
        likely_status=likely_status,
        proof_url=proof_url,
    )


def _raw_surfaces(data: dict[str, Any]) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    for issue in data.get("issues", []):
        if not isinstance(issue, dict):
            continue
        parent_issue = int(issue["number"]) if isinstance(issue.get("number"), int) else None
        parent_is_bounty = _is_bounty_issue(issue)
        if parent_is_bounty:
            surfaces.append(
                {
                    "text": "\n".join(
                        [str(issue.get("title") or ""), str(issue.get("body") or "")]
                    ),
                    "source_url": issue.get("url"),
                    "claimant": _author_login(issue.get("author")),
                    "source_type": "bounty_issue",
                    "fallback_bounty_issue": parent_issue,
                }
            )
        comments = issue.get("comments", [])
        if isinstance(comments, list):
            for comment in comments:
                if not isinstance(comment, dict):
                    continue
                surfaces.append(
                    {
                        "text": str(comment.get("body") or ""),
                        "source_url": comment.get("url"),
                        "claimant": _author_login(comment.get("author")),
                        "source_type": "bounty_issue_comment",
                        "fallback_bounty_issue": parent_issue if parent_is_bounty else None,
                    }
                )
    for pr in data.get("pull_requests", []):
        if not isinstance(pr, dict):
            continue
        pr_text = "\n".join([str(pr.get("title") or ""), str(pr.get("body") or "")])
        pr_bounty_issue = _first_bounty_ref(pr_text, None)
        surfaces.append(
            {
                "text": pr_text,
                "source_url": pr.get("url"),
                "claimant": _author_login(pr.get("author")),
                "source_type": "pull_request",
                "fallback_bounty_issue": pr_bounty_issue,
            }
        )
        for key, source_type in (
            ("comments", "pull_request_comment"),
            ("reviews", "pull_request_review"),
            ("review_comments", "pull_request_review_comment"),
        ):
            raw_items = pr.get(key, [])
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("body") or item.get("state") or "")
                surfaces.append(
                    {
                        "text": text,
                        "source_url": item.get("url") or pr.get("url"),
                        "claimant": _author_login(item.get("author")),
                        "source_type": source_type,
                        "fallback_bounty_issue": _first_bounty_ref(text, pr_bounty_issue),
                    }
                )
    return surfaces


def analyze_inventory(data: dict[str, Any], *, api_host: str = DEFAULT_API_HOST) -> dict[str, Any]:
    bounties = _bounty_index(data)
    proof_by_source = _proof_sources(data, api_host)
    surfaces = _raw_surfaces(data)
    keys: list[str] = []
    for surface in surfaces:
        text = str(surface.get("text") or "")
        source_url = _normalize_url(str(surface.get("source_url") or ""))
        fallback_bounty_issue = surface.get("fallback_bounty_issue")
        if not source_url or not _is_candidate(
            text,
            parent_is_bounty=fallback_bounty_issue is not None,
        ):
            continue
        keys.append(
            _duplicate_key(
                text,
                source_url,
                _first_bounty_ref(text, fallback_bounty_issue),
            )
        )
    duplicate_counts = Counter(keys)
    rows: list[ClaimRow] = []
    seen_sources: set[tuple[str, str]] = set()
    for surface in surfaces:
        row = _surface_row(
            text=str(surface.get("text") or ""),
            source_url=str(surface.get("source_url") or ""),
            claimant=str(surface.get("claimant") or "unknown"),
            source_type=str(surface.get("source_type") or "unknown"),
            fallback_bounty_issue=surface.get("fallback_bounty_issue"),
            bounties=bounties,
            proof_by_source=proof_by_source,
            duplicate_counts=duplicate_counts,
        )
        if row is None:
            continue
        identity = (row.source_url, row.source_type)
        if identity in seen_sources:
            continue
        seen_sources.add(identity)
        rows.append(row)
    rows.sort(key=lambda item: (item.bounty_issue or 0, item.duplicate_key, item.source_url))
    status_counts = Counter(row.likely_status for row in rows)
    return {
        "summary": {
            "rows": len(rows),
            "bounty_issues": len(bounties),
            "already_paid": status_counts["already_paid"],
            "unpaid_candidate": status_counts["unpaid_candidate"],
            "duplicate_candidate": status_counts["duplicate_candidate"],
            "missing_bounty_ref": status_counts["missing_bounty_ref"],
            "unknown_bounty": status_counts["unknown_bounty"],
            "ignored_or_unclear": status_counts["ignored_or_unclear"],
        },
        "likely_status_enum": [
            "already_paid",
            "unpaid_candidate",
            "duplicate_candidate",
            "missing_bounty_ref",
            "unknown_bounty",
            "ignored_or_unclear",
        ],
        "rows": [asdict(row) for row in rows],
    }


def format_markdown_report(report: dict[str, Any]) -> str:
    lines = ["## Claim Inventory", ""]
    for key, value in report["summary"].items():
        lines.append(f"- **{key.replace('_', ' ')}**: {value}")
    lines.append("")
    lines.append("| Status | Bounty | Claimant | Type | Source | Proof |")
    lines.append("| --- | ---: | --- | --- | --- | --- |")
    for row in report["rows"]:
        bounty = row["bounty_issue"] if row["bounty_issue"] is not None else ""
        source = f"[source]({row['source_url']})"
        proof = f"[proof]({row['proof_url']})" if row.get("proof_url") else ""
        lines.append(
            f"| `{row['likely_status']}` | {bounty} | {row['claimant']} | "
            f"{row['source_type']} | {source} | {proof} |"
        )
    return "\n".join(lines)


def _run_gh_json(args: list[str]) -> Any:
    if args[:2] == ["gh", "api"]:
        for flag in ("--method", "-X"):
            if flag not in args:
                continue
            index = args.index(flag)
            if index + 1 >= len(args) or args[index + 1].upper() not in {"GET", "HEAD"}:
                raise RuntimeError(f"refusing non-read-only gh api command: {' '.join(args)}")
    if any(arg in {"issue", "pr"} for arg in args) and any(
        arg in {"comment", "edit", "close", "reopen", "merge", "review"} for arg in args
    ):
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


def _load_pr_review_comments(repo: str, pr_number: int) -> list[dict[str, Any]]:
    raw_comments = _run_gh_json(
        [
            "gh",
            "api",
            f"repos/{repo}/pulls/{pr_number}/comments?per_page=100",
        ]
    )
    comments: list[dict[str, Any]] = []
    if not isinstance(raw_comments, list):
        return comments
    for item in raw_comments:
        if not isinstance(item, dict):
            continue
        comments.append(
            {
                "url": item.get("html_url") or item.get("url"),
                "author": item.get("user"),
                "body": item.get("body"),
            }
        )
    return comments


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
    data: dict[str, Any] = {}
    if isinstance(bounties, list):
        data["bounties"] = bounties
    if isinstance(activity, dict):
        contributors = activity.get("contributors")
        if isinstance(contributors, list):
            data["contributors"] = contributors
        recent = activity.get("recent")
        if isinstance(recent, list):
            data["recent"] = recent
    return data


def load_live_inventory(repo: str, api_host: str) -> dict[str, Any]:
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
        if (
            not isinstance(issue, dict)
            or not isinstance(issue.get("number"), int)
            or not _is_bounty_issue(issue)
        ):
            continue
        issue_view = _run_gh_json(
            [
                "gh",
                "issue",
                "view",
                str(issue["number"]),
                "--repo",
                repo,
                "--json",
                "number,title,url,body,labels,author,comments",
            ]
        )
        issues.append(issue_view)
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
            str(GH_LIMIT),
            "--json",
            "number,title,url,body,author,labels",
        ]
    )
    pull_requests: list[dict[str, Any]] = []
    for pr in prs:
        if not isinstance(pr, dict) or not isinstance(pr.get("number"), int):
            continue
        pr_view = _run_gh_json(
            [
                "gh",
                "pr",
                "view",
                str(pr["number"]),
                "--repo",
                repo,
                "--json",
                "number,title,url,body,author,labels,comments,reviews",
            ]
        )
        if isinstance(pr_view, dict):
            pr_view["review_comments"] = _load_pr_review_comments(repo, pr["number"])
        pull_requests.append(pr_view)
    public_state = load_public_api_state(api_host)
    public_state.update({"issues": issues, "pull_requests": pull_requests})
    return public_state


def _load_input(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("claim inventory input must be a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inventory public MergeWork claim surfaces and payout status."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Read public claim fixture JSON.")
    source.add_argument("--repo", help="Collect live public state with read-only gh calls.")
    parser.add_argument("--api-host", default=DEFAULT_API_HOST)
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = parser.parse_args(argv)

    data = _load_input(args.input) if args.input else load_live_inventory(args.repo, args.api_host)
    report = analyze_inventory(data, api_host=args.api_host)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_markdown_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
