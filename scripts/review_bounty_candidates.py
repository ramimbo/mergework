from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from typing import Any

DIRTY_MERGE_STATES = {"blocked", "conflicting", "dirty"}
GH_TIMEOUT_SECONDS = 30
GH_PR_SAFETY_CAP = 201
STANDARD_QUALITY_CHECK = "Quality, readiness, docs, and image checks"
HUMAN_REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}


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
        "mergeStateStatus": merge_state,
        "standard_quality_check": quality_state,
        "labels": labels,
        "current_head_human_reviews": len(current_reviews),
        "latest_human_review": _review_summary(latest_human_review),
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
    rows = [
        _classify_pr(raw, reviewer=reviewer_login, sufficient_reviews=sufficient_reviews)
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
        lines.append(
            f"- PR #{row['pull_request']}: {row['state']} - "
            f"{_single_line(row['title'])} ({_single_line(row['reason'])})"
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
        lines.append(
            f"- {label}: `{row['state']}` - {_single_line(row['title'])} "
            f"({_single_line(row['reason'])})"
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


def load_live_candidates(repo: str) -> dict[str, Any]:
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
    return {"pull_requests": prs}


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
    parser.add_argument("--sufficient-reviews", type=int, default=1)
    parser.add_argument("--format", choices=["json", "markdown", "text"], default="text")
    args = parser.parse_args(argv)

    data = _load_input(args.input) if args.input else load_live_candidates(args.repo)
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
