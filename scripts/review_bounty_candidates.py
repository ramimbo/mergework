from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from typing import Any

DIRTY_MERGE_STATES = {"blocked", "conflicting", "dirty"}
GH_TIMEOUT_SECONDS = 30
GH_PR_SAFETY_CAP = 201
STANDARD_QUALITY_CHECK = "Quality, readiness, docs, and image checks"
HUMAN_REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}
PR_URL_RE = re.compile(
    r"https://github\.com/[^/\s]+/[^/\s]+/pull/(?P<number>\d+)(?:#(?P<fragment>[A-Za-z0-9_-]+))?",
    re.IGNORECASE,
)
BARE_PR_RE = re.compile(r"\b(?:PR|pull request|pull)\s*#(?P<number>\d+)\b", re.IGNORECASE)
SHA_RE = r"[0-9a-f]{7,40}"
HEAD_SHA_RE = re.compile(
    rf"\b(?:head(?:refoid| sha)?|commit)\b\s*[:=`'\"]+\s*`?(?P<sha>{SHA_RE})",
    re.IGNORECASE,
)
BASE_SHA_RE = re.compile(
    rf"\b(?:base|main|origin/main|base/main)\b(?:\s+sha|\s+commit)?"
    rf"\s*[:=`'\"]+\s*`?(?P<sha>{SHA_RE})",
    re.IGNORECASE,
)


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


def _comment_author(raw: dict[str, Any]) -> Any:
    return raw.get("author", raw.get("user"))


def _comment_created_at(raw: dict[str, Any]) -> str | None:
    value = raw.get("createdAt", raw.get("created_at"))
    return str(value) if value else None


def _comment_url(raw: dict[str, Any]) -> str | None:
    value = raw.get("url", raw.get("html_url"))
    return str(value) if value else None


def _first_match(pattern: re.Pattern[str], body: str) -> str | None:
    match = pattern.search(body)
    return match.group("sha") if match else None


def _claim_evidence_type(fragment: str | None, body: str) -> str:
    clean_fragment = (fragment or "").lower()
    body_lower = body.lower()
    if clean_fragment.startswith("pullrequestreview-"):
        return "pr_review"
    if clean_fragment.startswith("issuecomment-") or "pr comment" in body_lower:
        return "pr_comment"
    return "pr_reference"


def _claim_from_comment(
    comment: dict[str, Any],
    *,
    pull_request: int,
    evidence_type: str,
    evidence_url: str | None,
) -> dict[str, Any]:
    body = str(comment.get("body") or "")
    return {
        "pull_request": pull_request,
        "url": _comment_url(comment),
        "author": _display_login(_comment_author(comment)),
        "created_at": _comment_created_at(comment),
        "evidence_type": evidence_type,
        "evidence_url": evidence_url,
        "head_sha": _first_match(HEAD_SHA_RE, body),
        "base_sha": _first_match(BASE_SHA_RE, body),
    }


def extract_bounty_claims(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    seen: set[tuple[int, str | None, str | None]] = set()
    for comment in comments:
        body = str(comment.get("body") or "")
        if "/claim" not in body.lower():
            continue
        for match in PR_URL_RE.finditer(body):
            pull_request = int(match.group("number"))
            evidence_url = match.group(0)
            evidence_type = _claim_evidence_type(match.group("fragment"), body)
            url_key: tuple[int, str | None, str | None] = (
                pull_request,
                evidence_url,
                _comment_url(comment),
            )
            if url_key in seen:
                continue
            seen.add(url_key)
            claims.append(
                _claim_from_comment(
                    comment,
                    pull_request=pull_request,
                    evidence_type=evidence_type,
                    evidence_url=evidence_url,
                )
            )
        for match in BARE_PR_RE.finditer(body):
            pull_request = int(match.group("number"))
            bare_key: tuple[int, str | None, str | None] = (
                pull_request,
                None,
                _comment_url(comment),
            )
            if bare_key in seen:
                continue
            seen.add(bare_key)
            claims.append(
                _claim_from_comment(
                    comment,
                    pull_request=pull_request,
                    evidence_type="pr_reference",
                    evidence_url=None,
                )
            )
    return claims


def _claims_by_pr(data: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    comments = data.get("bounty_claim_comments", [])
    if not isinstance(comments, list):
        return {}
    claims_by_pr: dict[int, list[dict[str, Any]]] = {}
    valid_comments = [comment for comment in comments if isinstance(comment, dict)]
    for claim in extract_bounty_claims(valid_comments):
        claims_by_pr.setdefault(int(claim["pull_request"]), []).append(claim)
    return claims_by_pr


def _claim_summaries(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "url": claim["url"],
            "author": claim["author"],
            "created_at": claim["created_at"],
            "evidence_type": claim["evidence_type"],
            "evidence_url": claim["evidence_url"],
            "head_sha": claim["head_sha"],
            "base_sha": claim["base_sha"],
        }
        for claim in claims
    ]


def _classify_pr(
    raw: dict[str, Any],
    *,
    reviewer: str,
    sufficient_reviews: int,
    claims_by_pr: dict[int, list[dict[str, Any]]],
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
    bounty_claims = claims_by_pr.get(number, [])
    current_head_claims = [
        claim for claim in bounty_claims if claim.get("head_sha") and claim["head_sha"] == head_oid
    ]
    pr_comment_claims = [
        claim for claim in bounty_claims if claim.get("evidence_type") == "pr_comment"
    ]
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
    elif current_head_claims:
        state = "already_claimed_current_head"
        reason = "active bounty issue has a claim tied to the current head"
    elif bounty_claims and merge_state in DIRTY_MERGE_STATES:
        state = "claimed_stale_head_or_base"
        reason = "active bounty issue has claim evidence, but current merge state is dirty"
    elif pr_comment_claims:
        state = "claimed_by_pr_comment"
        reason = "active bounty issue has a concise PR comment claim"
    elif bounty_claims:
        state = "already_claimed_on_bounty_issue"
        reason = "active bounty issue already references this PR"
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
        "mergeStateStatus": merge_state,
        "standard_quality_check": quality_state,
        "labels": labels,
        "current_head_human_reviews": len(current_reviews),
        "latest_human_review": _review_summary(latest_human_review),
        "bounty_claim_count": len(bounty_claims),
        "bounty_claims": _claim_summaries(bounty_claims),
    }


def analyze_candidates(
    data: dict[str, Any],
    *,
    reviewer: str,
    sufficient_reviews: int = 1,
) -> dict[str, Any]:
    reviewer_login = reviewer.strip().lower()
    if not reviewer_login:
        raise ValueError("reviewer must not be empty")
    if sufficient_reviews < 1:
        raise ValueError("sufficient_reviews must be at least 1")
    claim_index = _claims_by_pr(data)
    rows = [
        _classify_pr(
            raw,
            reviewer=reviewer_login,
            sufficient_reviews=sufficient_reviews,
            claims_by_pr=claim_index,
        )
        for raw in data.get("pull_requests", [])
        if isinstance(raw, dict) and isinstance(raw.get("number"), int)
    ]
    counts = Counter(row["state"] for row in rows)
    return {
        "reviewer": reviewer_login,
        "summary": {
            "pull_requests": len(rows),
            **{key: counts.get(key, 0) for key in sorted(counts)},
        },
        "pull_requests": rows,
    }


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def format_text_report(report: dict[str, Any]) -> str:
    lines = [f"Review bounty candidates for {report['reviewer']}"]
    for key, value in report["summary"].items():
        lines.append(f"- {key.replace('_', ' ')}: {value}")
    for row in report["pull_requests"]:
        claim_suffix = ""
        bounty_claims = row.get("bounty_claims", [])
        if isinstance(bounty_claims, list) and bounty_claims:
            claim_urls = [
                str(claim.get("url"))
                for claim in bounty_claims
                if isinstance(claim, dict) and claim.get("url")
            ]
            if claim_urls:
                claim_suffix = f" claims: {', '.join(claim_urls[:2])}"
        lines.append(
            f"- PR #{row['pull_request']}: {row['state']} - "
            f"{_single_line(row['title'])} ({_single_line(row['reason'])}){claim_suffix}"
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
        claim_suffix = ""
        bounty_claims = row.get("bounty_claims", [])
        if isinstance(bounty_claims, list) and bounty_claims:
            claim_links = [
                f"[claim]({claim['url']})"
                for claim in bounty_claims[:2]
                if isinstance(claim, dict) and claim.get("url")
            ]
            if claim_links:
                claim_suffix = f"; {' '.join(claim_links)}"
        lines.append(
            f"- {label}: `{row['state']}` - {_single_line(row['title'])} "
            f"({_single_line(row['reason'])}){claim_suffix}"
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


def _run_gh_api_paginated_array(endpoint: str) -> list[dict[str, Any]]:
    pages = _run_gh_json(["gh", "api", "--paginate", "--slurp", endpoint])
    if not isinstance(pages, list):
        raise RuntimeError("gh api paginated response must be a JSON array")
    rows: list[dict[str, Any]] = []
    for page in pages:
        if isinstance(page, list):
            rows.extend([item for item in page if isinstance(item, dict)])
        elif isinstance(page, dict):
            rows.append(page)
    return rows


def load_bounty_claim_comments(repo: str, issue_number: int) -> list[dict[str, Any]]:
    comments = _run_gh_api_paginated_array(f"repos/{repo}/issues/{issue_number}/comments")
    return [
        {
            "url": comment.get("html_url"),
            "author": comment.get("user"),
            "createdAt": comment.get("created_at"),
            "body": comment.get("body"),
        }
        for comment in comments
    ]


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
    data = {"pull_requests": prs}
    if bounty_issue is not None:
        data["bounty_claim_comments"] = load_bounty_claim_comments(repo, bounty_issue)
    return data


def _load_input(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("candidate input must be a JSON object")
    return data


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
        help="When using --repo, read active bounty issue claim comments and join them to PRs.",
    )
    parser.add_argument("--sufficient-reviews", type=int, default=1)
    parser.add_argument("--format", choices=["json", "markdown", "text"], default="text")
    args = parser.parse_args(argv)

    if args.input and args.bounty_issue is not None:
        raise ValueError("--bounty-issue is only available with --repo live mode")
    data = (
        _load_input(args.input)
        if args.input
        else load_live_candidates(args.repo, bounty_issue=args.bounty_issue)
    )
    report = analyze_candidates(
        data,
        reviewer=args.reviewer,
        sufficient_reviews=args.sufficient_reviews,
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
