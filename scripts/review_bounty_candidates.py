from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GH_TIMEOUT_SECONDS = 30

DIRTY_MERGE_STATES = {"blocked", "conflicting", "dirty"}
GH_PR_SAFETY_CAP = 201
STANDARD_QUALITY_CHECK = "Quality, readiness, docs, and image checks"
HUMAN_REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}
CLAIM_SIGNAL_RE = re.compile(
    r"(^|\s)(/claim|claim(?:ing)?|reviewed|review bounty|evidence)(\b|\s|:)",
    re.IGNORECASE,
)
PR_REVIEW_EVIDENCE_RE = re.compile(
    r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)#pullrequestreview-\d+",
    re.IGNORECASE,
)
PR_COMMENT_EVIDENCE_RE = re.compile(
    r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)#issuecomment-\d+",
    re.IGNORECASE,
)
PR_LINK_RE = re.compile(
    r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)",
    re.IGNORECASE,
)
PR_NUMBER_REF_RE = re.compile(r"\bpull/(\d+)\b|\bPR\s+#(\d+)\b", re.IGNORECASE)
LABELED_HEAD_SHA_RE = re.compile(
    r"(?:head(?:RefOid| ref| sha| oid)?|head)\s*[:=]\s*[`']?([0-9a-f]{40})[`']?",
    re.IGNORECASE,
)
LABELED_BASE_SHA_RE = re.compile(
    r"(?:base(?:RefOid| ref| sha| oid)?|origin/main|main sha|main)"
    r"\s*[:=]\s*[`']?([0-9a-f]{40})[`']?",
    re.IGNORECASE,
)
SATURATION_PROTECTED_STATES = {
    "self_authored",
    "needs_info",
    "already_reviewed_current_head_by_reviewer",
    "already_has_sufficient_current_head_human_reviews",
    "waiting_for_author_update",
}


def _login(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip().lower()
    if isinstance(raw, dict):
        login = raw.get("login")
        if isinstance(login, str):
            return login.strip().lower()
    return ""


def _display_login(raw: Any) -> str:
    login = _login(raw)
    return login or "unknown"


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


def _merge_state(raw: dict[str, Any]) -> str:
    for key in ("merge_state", "mergeStateStatus", "mergeable", "mergeable_state"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value.lower()
    return "unknown"


def _head_oid(raw: dict[str, Any]) -> str:
    for key in ("headRefOid", "head_ref_oid", "head_sha", "head"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _base_oid(raw: dict[str, Any]) -> str:
    for key in ("baseRefOid", "base_ref_oid", "base_sha", "base"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _comment_url(comment: dict[str, Any]) -> str:
    for key in ("html_url", "url"):
        value = comment.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _repo_matches(owner: str, name: str, repo: str) -> bool:
    try:
        expected_owner, expected_name = repo.split("/", 1)
    except ValueError:
        return False
    return owner.lower() == expected_owner.lower() and name.lower() == expected_name.lower()


def _parse_claim_comment(comment: dict[str, Any], *, repo: str) -> list[dict[str, Any]]:
    body = str(comment.get("body") or "")
    if not body.strip():
        return []
    if not (
        CLAIM_SIGNAL_RE.search(body)
        or PR_REVIEW_EVIDENCE_RE.search(body)
        or PR_COMMENT_EVIDENCE_RE.search(body)
    ):
        return []

    pr_evidence: dict[int, tuple[str, str]] = {}
    for match in PR_REVIEW_EVIDENCE_RE.finditer(body):
        if _repo_matches(match.group(1), match.group(2), repo):
            pr_evidence[int(match.group(3))] = (match.group(0), "pr_review")
    for match in PR_COMMENT_EVIDENCE_RE.finditer(body):
        if _repo_matches(match.group(1), match.group(2), repo):
            pr = int(match.group(3))
            pr_evidence.setdefault(pr, (match.group(0), "pr_comment"))
    for match in PR_LINK_RE.finditer(body):
        if _repo_matches(match.group(1), match.group(2), repo):
            pr = int(match.group(3))
            pr_evidence.setdefault(pr, (match.group(0).split("#", 1)[0], "pr_reference"))
    for match in PR_NUMBER_REF_RE.finditer(body):
        pr_str = match.group(1) or match.group(2)
        if pr_str:
            pr = int(pr_str)
            pr_evidence.setdefault(
                pr,
                (f"https://github.com/{repo}/pull/{pr}", "pr_reference"),
            )

    if not pr_evidence:
        return []

    head_sha = None
    base_sha = None
    labeled_head = LABELED_HEAD_SHA_RE.search(body)
    if labeled_head:
        head_sha = labeled_head.group(1).lower()
    labeled_base = LABELED_BASE_SHA_RE.search(body)
    if labeled_base:
        base_sha = labeled_base.group(1).lower()

    claim_url = _comment_url(comment)
    claimant = _display_login(comment.get("author") or comment.get("user"))
    submitted_at = comment.get("created_at") or comment.get("createdAt")
    records: list[dict[str, Any]] = []
    for pr, (evidence_url, evidence_kind) in sorted(pr_evidence.items()):
        records.append(
            {
                "pull_request": pr,
                "claim_url": claim_url,
                "evidence_url": evidence_url,
                "evidence_kind": evidence_kind,
                "head_sha": head_sha,
                "base_sha": base_sha,
                "claimant": claimant,
                "submitted_at": submitted_at,
            }
        )
    return records


def index_bounty_claims(comments: list[Any], *, repo: str) -> dict[int, list[dict[str, Any]]]:
    by_pr: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        for record in _parse_claim_comment(comment, repo=repo):
            by_pr[int(record["pull_request"])].append(record)
    return dict(by_pr)


def _check_name(check: dict[str, Any]) -> str:
    return str(check.get("name") or check.get("context") or check.get("workflowName") or "")


def _check_state(check: dict[str, Any]) -> str:
    return str(check.get("conclusion") or check.get("state") or check.get("status") or "").upper()


def _standard_quality_state(raw: dict[str, Any]) -> str:
    checks = raw.get("statusCheckRollup", raw.get("status_checks", []))
    if not isinstance(checks, list):
        return "missing"
    for check in checks:
        if isinstance(check, dict) and _check_name(check) == STANDARD_QUALITY_CHECK:
            state = _check_state(check)
            if state in {"SUCCESS", "PASS"}:
                return "success"
            if state:
                return state.lower()
            return "pending"
    return "missing"


def _review_commit(review: dict[str, Any]) -> str:
    commit = review.get("commit")
    if isinstance(commit, dict):
        for key in ("oid", "sha"):
            value = commit.get(key)
            if isinstance(value, str) and value:
                return value
    for key in ("commit_id", "commitId", "commit_oid"):
        value = review.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _is_bot_author(raw: Any) -> bool:
    if isinstance(raw, dict):
        if raw.get("is_bot") is True:
            return True
        login = _login(raw)
    else:
        login = _login(raw)
    return login.endswith("[bot]") or login in {"coderabbitai", "github-actions"}


def _human_reviews(raw: dict[str, Any], pr_author: str) -> list[dict[str, Any]]:
    reviews = raw.get("reviews", [])
    if not isinstance(reviews, list):
        return []
    useful: list[dict[str, Any]] = []
    for review in reviews:
        if not isinstance(review, dict):
            continue
        author = review.get("author")
        login = _login(author)
        state = str(review.get("state") or "").upper()
        if not login or login == pr_author or state not in HUMAN_REVIEW_STATES:
            continue
        if _is_bot_author(author):
            continue
        useful.append(review)
    return useful


def _review_summary(review: dict[str, Any] | None) -> dict[str, str | None]:
    if review is None:
        return {"reviewer": None, "state": None, "commit": None}
    return {
        "reviewer": _display_login(review.get("author")),
        "state": str(review.get("state") or "").upper() or None,
        "commit": _review_commit(review) or None,
    }


def _latest_review(reviews: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not reviews:
        return None
    return reviews[-1]


def _classify_pr(
    raw: dict[str, Any],
    *,
    reviewer: str,
    sufficient_reviews: int,
) -> dict[str, Any]:
    number = int(raw["number"])
    title = str(raw.get("title") or "")
    pr_author = _login(raw.get("author"))
    labels = _labels(raw)
    normalized_labels = {label.lower() for label in labels}
    merge_state = _merge_state(raw)
    head_oid = _head_oid(raw)
    quality_state = _standard_quality_state(raw)
    reviews = _human_reviews(raw, pr_author)
    current_reviews = [review for review in reviews if _review_commit(review) == head_oid]
    current_reviewer_reviews = [
        review for review in current_reviews if _login(review.get("author")) == reviewer
    ]
    reviewer_reviews = [review for review in reviews if _login(review.get("author")) == reviewer]
    latest_human_review = _latest_review(reviews)
    latest_reviewer_review = _latest_review(reviewer_reviews)
    changes_requested = any(
        str(review.get("state") or "").upper() == "CHANGES_REQUESTED" for review in current_reviews
    )

    state = "candidate_for_fresh_review"
    reason = "no current-head human review found"
    if pr_author == reviewer:
        state = "self_authored"
        reason = "reviewer authored this PR"
    elif "mrwk:needs-info" in normalized_labels:
        state = "needs_info"
        reason = "PR has mrwk:needs-info label"
    elif merge_state in DIRTY_MERGE_STATES:
        state = "dirty_or_conflicted"
        reason = f"merge state is {merge_state}"
    elif quality_state != "success":
        state = "missing_standard_quality_check"
        reason = f"standard quality check is {quality_state}"
    elif current_reviewer_reviews:
        state = "already_reviewed_current_head_by_reviewer"
        reason = "reviewer already reviewed current head"
    elif changes_requested:
        state = "waiting_for_author_update"
        reason = "current-head human review already requested changes"
    elif len(current_reviews) >= sufficient_reviews:
        state = "already_has_sufficient_current_head_human_reviews"
        reason = f"{len(current_reviews)} current-head human review(s) already present"
    elif latest_reviewer_review is not None:
        reason = "reviewer last reviewed an older head"
    elif latest_human_review is not None:
        reason = "latest useful human review is stale"

    return {
        "pull_request": number,
        "title": title,
        "url": raw.get("url"),
        "author": _display_login(raw.get("author")),
        "state": state,
        "reason": reason,
        "headRefOid": head_oid or None,
        "baseRefOid": _base_oid(raw) or None,
        "mergeStateStatus": merge_state,
        "standard_quality_check": quality_state,
        "labels": labels,
        "current_head_human_reviews": len(current_reviews),
        "latest_human_review": _review_summary(latest_human_review),
    }


def _attach_claim_metadata(row: dict[str, Any], claims: list[dict[str, Any]]) -> None:
    if not claims:
        return
    row["bounty_claims"] = [
        {key: value for key, value in claim.items() if key != "pull_request"} for claim in claims
    ]
    row["matched_claim_urls"] = [
        str(claim["claim_url"]) for claim in claims if claim.get("claim_url")
    ]
    latest = claims[-1]
    if latest.get("evidence_kind"):
        row["claim_evidence_kind"] = latest["evidence_kind"]
    if latest.get("evidence_url"):
        row["matched_evidence_urls"] = [
            str(claim["evidence_url"]) for claim in claims if claim.get("evidence_url")
        ]


def _apply_bounty_saturation(
    row: dict[str, Any],
    *,
    claims: list[dict[str, Any]],
    merge_state: str,
    head_oid: str,
    base_oid: str,
) -> dict[str, Any]:
    if not claims:
        if merge_state in DIRTY_MERGE_STATES and row["state"] == "dirty_or_conflicted":
            row["state"] = "dirty_unclaimed_current_base_candidate"
            row["reason"] = (
                "dirty/conflicted PR has no review-bounty claim; "
                "current-base follow-up may be useful"
            )
        return row

    _attach_claim_metadata(row, claims)
    if row["state"] in SATURATION_PROTECTED_STATES:
        return row

    latest = claims[-1]
    claim_head = str(latest.get("head_sha") or "").lower()
    claim_base = str(latest.get("base_sha") or "").lower()
    head = head_oid.lower()
    base = base_oid.lower()
    evidence_kind = str(latest.get("evidence_kind") or "")

    stale = False
    stale_reason = "review-bounty claim may be stale"
    if claim_head and head and claim_head != head:
        stale = True
        stale_reason = f"claim head {claim_head[:7]} differs from current head {head[:7]}"
    elif claim_base and base and claim_base != base:
        stale = True
        stale_reason = f"claim base {claim_base[:7]} differs from current base {base[:7]}"
    elif merge_state in DIRTY_MERGE_STATES and claim_head and head and claim_head == head:
        stale = True
        stale_reason = "PR is dirty/conflicted after a clean-current-head bounty claim"

    if stale:
        row["state"] = "claimed_stale_head_or_base"
        row["reason"] = stale_reason
    elif evidence_kind == "pr_comment":
        row["state"] = "claimed_by_pr_comment"
        row["reason"] = "review-bounty claim uses PR comment evidence"
    elif claim_head and head and claim_head == head:
        row["state"] = "already_claimed_current_head"
        row["reason"] = "review-bounty claim matches current PR head"
    else:
        row["state"] = "already_claimed_on_bounty_issue"
        row["reason"] = "PR already referenced on review bounty issue"
    return row


def analyze_candidates(
    data: dict[str, Any],
    *,
    reviewer: str,
    sufficient_reviews: int = 1,
    repo: str | None = None,
) -> dict[str, Any]:
    reviewer_login = reviewer.strip().lower()
    if not reviewer_login:
        raise ValueError("reviewer must not be empty")
    if sufficient_reviews < 1:
        raise ValueError("sufficient_reviews must be at least 1")

    effective_repo = repo or (data.get("repo") if isinstance(data.get("repo"), str) else None)
    claims_by_pr: dict[int, list[dict[str, Any]]] = {}
    raw_comments = data.get("bounty_claim_comments")
    saturation_enabled = isinstance(effective_repo, str) and "bounty_claim_comments" in data
    if saturation_enabled and isinstance(raw_comments, list):
        claims_by_pr = index_bounty_claims(raw_comments, repo=effective_repo)

    rows: list[dict[str, Any]] = []
    for raw in data.get("pull_requests", []):
        if not isinstance(raw, dict) or not isinstance(raw.get("number"), int):
            continue
        row = _classify_pr(raw, reviewer=reviewer_login, sufficient_reviews=sufficient_reviews)
        if saturation_enabled:
            row = _apply_bounty_saturation(
                row,
                claims=claims_by_pr.get(int(raw["number"]), []),
                merge_state=_merge_state(raw),
                head_oid=_head_oid(raw),
                base_oid=_base_oid(raw),
            )
        rows.append(row)

    counts = Counter(row["state"] for row in rows)
    report: dict[str, Any] = {
        "reviewer": reviewer_login,
        "summary": {
            "pull_requests": len(rows),
            **{key: counts.get(key, 0) for key in sorted(counts)},
        },
        "pull_requests": rows,
    }
    if effective_repo and saturation_enabled:
        report["bounty_issue_claims_indexed"] = sum(len(items) for items in claims_by_pr.values())
    return report


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def format_text_report(report: dict[str, Any]) -> str:
    lines = [f"Review bounty candidates for {report['reviewer']}"]
    for key, value in report["summary"].items():
        lines.append(f"- {key.replace('_', ' ')}: {value}")
    for row in report["pull_requests"]:
        claim_note = ""
        if row.get("matched_claim_urls"):
            claim_note = f" claims={','.join(row['matched_claim_urls'])}"
        lines.append(
            f"- PR #{row['pull_request']}: {row['state']} - "
            f"{_single_line(row['title'])} ({_single_line(row['reason'])}){claim_note}"
        )
    return "\n".join(lines)


def format_markdown_report(report: dict[str, Any]) -> str:
    lines = [f"## Review Bounty Candidates For `{report['reviewer']}`", ""]
    for key, value in report["summary"].items():
        lines.append(f"- **{key.replace('_', ' ')}**: {value}")
    for row in report["pull_requests"]:
        label = f"PR #{row['pull_request']}"
        if row.get("url"):
            label = f"[{label}]({row['url']})"
        claim_note = ""
        if row.get("matched_claim_urls"):
            urls = ", ".join(f"`{url}`" for url in row["matched_claim_urls"])
            claim_note = f" Claims: {urls}."
        lines.append(
            f"- {label}: `{row['state']}` - {_single_line(row['title'])} "
            f"({_single_line(row['reason'])}){claim_note}"
        )
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
    except FileNotFoundError as exc:
        raise RuntimeError(
            "GitHub CLI executable 'gh' was not found; install gh and ensure it is on PATH "
            "before using live --repo mode"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "gh command failed "
            f"(exit {exc.returncode}): {command}\n"
            f"stdout:\n{exc.stdout or exc.output or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        ) from exc
    return json.loads(completed.stdout)


def load_live_candidates(repo: str, *, bounty_issue: int | None = None) -> dict[str, Any]:
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
            ",".join(
                [
                    "number",
                    "title",
                    "url",
                    "author",
                    "headRefOid",
                    "baseRefOid",
                    "mergeStateStatus",
                    "labels",
                    "statusCheckRollup",
                    "reviews",
                ]
            ),
        ]
    )
    if len(prs) >= GH_PR_SAFETY_CAP:
        raise RuntimeError(
            f"gh pr list reached the {GH_PR_SAFETY_CAP} item safety cap; "
            "use an API-paginated collector before trusting this live report"
        )
    data: dict[str, Any] = {"repo": repo, "pull_requests": prs}
    if bounty_issue is not None:
        issue = _run_gh_json(
            [
                "gh",
                "issue",
                "view",
                str(bounty_issue),
                "--repo",
                repo,
                "--json",
                "comments",
            ]
        )
        comments = issue.get("comments", []) if isinstance(issue, dict) else []
        if not isinstance(comments, list):
            comments = []
        data["bounty_claim_comments"] = comments
    return data


def _load_input(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("candidate input must be a JSON object")
    return data


def _require_non_empty_arg(parser: argparse.ArgumentParser, option_name: str, value: str) -> str:
    stripped = value.strip()
    if not stripped:
        parser.error(f"{option_name} must be a non-empty value")
    if stripped != value:
        parser.error(f"{option_name} must not include leading or trailing whitespace")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rank open PRs for reviewer-specific review-bounty work."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Read candidate data from a JSON fixture file.")
    source.add_argument("--repo", help="Collect live open PR data with gh.")
    parser.add_argument("--reviewer", required=True, help="GitHub login of the reviewer.")
    parser.add_argument(
        "--bounty-issue",
        type=int,
        help="Active review-bounty issue number for claim saturation (live --repo mode only).",
    )
    parser.add_argument("--sufficient-reviews", type=int, default=1)
    parser.add_argument("--format", choices=["json", "markdown", "text"], default="text")
    args = parser.parse_args(argv)

    if args.input is not None and args.bounty_issue is not None:
        parser.error("--bounty-issue is only valid in live --repo mode")

    if args.input is not None:
        data = _load_input(_require_non_empty_arg(parser, "--input", args.input))
    else:
        if args.bounty_issue is not None and args.bounty_issue < 1:
            parser.error("--bounty-issue must be a positive integer")
        data = load_live_candidates(
            _require_non_empty_arg(parser, "--repo", args.repo),
            bounty_issue=args.bounty_issue,
        )
    report = analyze_candidates(
        data,
        reviewer=args.reviewer,
        sufficient_reviews=args.sufficient_reviews,
        repo=data.get("repo") if isinstance(data.get("repo"), str) else None,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.format == "markdown":
        print(format_markdown_report(report))
    else:
        print(format_text_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
