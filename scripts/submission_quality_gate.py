from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bounty_refs import BOUNTY_REF_RE, LEADING_BOUNTY_REF_RE

EVIDENCE_RE = re.compile(
    r"\b(pytest|ruff|mypy|validation|verified|test evidence|checks? passed)\b",
    re.IGNORECASE,
)
SUMMARY_RE = re.compile(r"\b(summary|what changed|changes?)\b", re.IGNORECASE)
GH_TIMEOUT_SECONDS = 30
DEFAULT_API_HOST = "https://api.mrwk.online"
DEFAULT_MAX_MAINTAINER_AGE_DAYS = 14
GH_PR_SAFETY_CAP = 101
GH_ISSUE_SAFETY_CAP = 201
MAINTAINER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
MAX_BOUNTY_REF = 2**63 - 1


def _check(name: str, status: str, message: str) -> dict[str, str]:
    return {"name": name, "status": status, "message": message}


def _bounty_refs(text: str) -> list[int]:
    refs: list[int] = []
    seen: set[int] = set()
    for match in BOUNTY_REF_RE.findall(text):
        try:
            ref = int(match)
        except ValueError:
            continue
        if ref > MAX_BOUNTY_REF:
            continue
        if ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
    return refs


def _bounty_is_payable(raw: dict[str, Any]) -> bool:
    if str(raw.get("state") or "").lower() not in {"", "open"}:
        return False
    remaining = raw.get("awards_remaining", raw.get("awardsRemaining"))
    if remaining is None:
        return True
    try:
        return int(remaining) > 0
    except (TypeError, ValueError):
        return False


def _bounty_payability_verified(raw: dict[str, Any]) -> bool:
    return raw.get("payability_verified", True) is not False


def _active_attempts_verified(raw: dict[str, Any]) -> bool:
    return raw.get("active_attempts_verified", True) is not False


def _safe_attempts(raw: dict[str, Any]) -> list[dict[str, Any]]:
    attempts = raw.get("active_attempts", [])
    if not isinstance(attempts, list):
        return []
    return [attempt for attempt in attempts if isinstance(attempt, dict)]


def _attempt_field(attempt: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = attempt.get(name)
        if value not in (None, ""):
            return value
    return None


def _format_attempt_summary(attempt: dict[str, Any]) -> str:
    parts: list[str] = []
    submitter = _attempt_field(attempt, "submitter", "submitter_account", "account", "github_login")
    if submitter:
        parts.append(f"submitter={submitter}")
    source_url = _attempt_field(attempt, "source_url", "public_source_url", "url")
    if source_url:
        parts.append(f"source={source_url}")
    status = _attempt_field(attempt, "status")
    if status:
        parts.append(f"status={status}")
    expires_at = _attempt_field(attempt, "expires_at", "expiresAt", "expiry_time")
    if expires_at:
        parts.append(f"expires={expires_at}")
    return ", ".join(parts) or "active attempt"


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _isoformat_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _current_time(data: dict[str, Any]) -> datetime:
    return _parse_datetime(data.get("now")) or datetime.now(UTC)


def _maintainer_activity_check(
    bounty_ref: int, bounty: dict[str, Any], now: datetime
) -> dict[str, str] | None:
    if "last_maintainer_activity_at" not in bounty and "maintainer_activity_verified" not in bounty:
        return None
    if bounty.get("maintainer_activity_verified") is False:
        return _check(
            "maintainer_activity",
            "warn",
            f"recent maintainer activity for bounty #{bounty_ref} could not be verified",
        )
    last_activity = _parse_datetime(bounty.get("last_maintainer_activity_at"))
    if last_activity is None:
        return _check(
            "maintainer_activity",
            "warn",
            f"recent maintainer activity for bounty #{bounty_ref} could not be verified",
        )
    try:
        max_age_days = int(bounty.get("max_maintainer_age_days", DEFAULT_MAX_MAINTAINER_AGE_DAYS))
    except (TypeError, ValueError):
        return _check(
            "maintainer_activity",
            "warn",
            f"recent maintainer activity for bounty #{bounty_ref} could not be verified",
        )
    delta = now - last_activity
    age_days = max(0, int(delta.total_seconds() // 86400))
    if delta > timedelta(days=max_age_days):
        return _check(
            "maintainer_activity",
            "warn",
            f"last maintainer activity for bounty #{bounty_ref} was {age_days} days ago",
        )
    return _check(
        "maintainer_activity",
        "pass",
        f"maintainer activity for bounty #{bounty_ref} was seen {age_days} days ago",
    )


def _title_from_submission(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip(" -:\t")
        if not clean:
            continue
        clean = LEADING_BOUNTY_REF_RE.sub("", clean).strip(" -:\t")
        if not clean:
            continue
        if SUMMARY_RE.search(clean) and len(clean.split()) <= 4:
            continue
        if BOUNTY_REF_RE.search(clean) or EVIDENCE_RE.search(clean):
            continue
        return " ".join(clean.lower().split())
    return ""


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left.lower(), right.lower()).ratio()


def _has_evidence(text: str) -> bool:
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        if SUMMARY_RE.search(clean) and ":" in clean:
            continue
        if EVIDENCE_RE.search(clean):
            return True
    return False


def _matching_pr_bounty_refs(pr: dict[str, Any]) -> list[int]:
    text = "\n".join(str(pr.get(key) or "") for key in ("title", "body"))
    return _bounty_refs(text)


def _similar_open_prs(
    pull_requests: list[dict[str, Any]], bounty_ref: int | None, submission_title: str
) -> list[dict[str, Any]]:
    if bounty_ref is None or not submission_title:
        return []
    matches: list[dict[str, Any]] = []
    for pr in pull_requests:
        if str(pr.get("state") or "OPEN").lower() not in {"open", "opened"}:
            continue
        if bounty_ref not in _matching_pr_bounty_refs(pr):
            continue
        title = str(pr.get("title") or "")
        if _similarity(submission_title, title) < 0.78:
            continue
        matches.append(
            {
                "number": pr.get("number"),
                "title": title,
                "url": pr.get("url"),
            }
        )
    return matches


def evaluate_submission(data: dict[str, Any]) -> dict[str, Any]:
    text = str(data.get("submission_text") or "")
    now = _current_time(data)
    bounties = {
        int(item["number"]): item
        for item in data.get("bounties", [])
        if isinstance(item, dict) and isinstance(item.get("number"), int)
    }
    pull_requests = [item for item in data.get("pull_requests", []) if isinstance(item, dict)]
    checks: list[dict[str, str]] = []
    load_warning = str(data.get("load_warning") or "").strip()
    if load_warning:
        checks.append(_check("source_completeness", "warn", load_warning))
    refs = _bounty_refs(text)
    bounty_ref = refs[0] if refs else None
    if bounty_ref is None:
        checks.append(
            _check(
                "bounty_reference",
                "fail",
                "submission text must include a bounty reference such as "
                "Bounty #<issue>, Refs #<issue>, Fixes #<issue>, or /claim #<issue>",
            )
        )
    else:
        checks.append(_check("bounty_reference", "pass", f"found bounty reference #{bounty_ref}"))
        if len(refs) > 1:
            joined_refs = ", ".join(f"#{ref}" for ref in refs)
            checks.append(
                _check(
                    "single_bounty_reference",
                    "warn",
                    f"submission references multiple bounties ({joined_refs}); "
                    "keep one bounty target or split the work",
                )
            )
        bounty = bounties.get(bounty_ref)
        if bounty is None:
            checks.append(
                _check(
                    "bounty_payable",
                    "warn",
                    f"referenced bounty #{bounty_ref} was not available in input",
                )
            )
        elif not _bounty_is_payable(bounty):
            checks.append(
                _check(
                    "bounty_payable",
                    "fail",
                    f"referenced bounty #{bounty_ref} is closed or exhausted",
                )
            )
        elif not _bounty_payability_verified(bounty):
            checks.append(
                _check(
                    "bounty_payable",
                    "warn",
                    f"referenced bounty #{bounty_ref} payability could not be verified",
                )
            )
        else:
            checks.append(
                _check("bounty_payable", "pass", f"referenced bounty #{bounty_ref} is open")
            )
        if bounty is not None:
            activity_check = _maintainer_activity_check(bounty_ref, bounty, now)
            if activity_check is not None:
                checks.append(activity_check)
            if "active_attempts" in bounty or "active_attempts_verified" in bounty:
                active_attempts = _safe_attempts(bounty)
                if active_attempts:
                    checks.append(
                        _check(
                            "active_attempts",
                            "warn",
                            f"{len(active_attempts)} active attempt(s) already exist "
                            f"for bounty #{bounty_ref}",
                        )
                    )
                elif not _active_attempts_verified(bounty):
                    checks.append(
                        _check(
                            "active_attempts",
                            "warn",
                            f"active attempts for bounty #{bounty_ref} could not be verified",
                        )
                    )
                else:
                    checks.append(
                        _check(
                            "active_attempts",
                            "pass",
                            f"no active attempts found for bounty #{bounty_ref}",
                        )
                    )

    if SUMMARY_RE.search(text):
        checks.append(_check("summary_present", "pass", "summary text found"))
    else:
        checks.append(_check("summary_present", "warn", "include a concise summary of the work"))

    if _has_evidence(text):
        checks.append(_check("evidence_present", "pass", "test or validation evidence found"))
    else:
        checks.append(
            _check(
                "evidence_present",
                "warn",
                "include concrete test or validation evidence before submission",
            )
        )

    similar = _similar_open_prs(pull_requests, bounty_ref, _title_from_submission(text))
    if similar:
        checks.append(
            _check(
                "similar_open_pr",
                "warn",
                "similar open PRs already reference this bounty",
            )
        )
    else:
        checks.append(_check("similar_open_pr", "pass", "no similar open PRs found"))

    if any(check["status"] == "fail" for check in checks):
        status = "fail"
    elif any(check["status"] == "warn" for check in checks):
        status = "warn"
    else:
        status = "pass"
    return {
        "status": status,
        "bounty_reference": bounty_ref,
        "checks": checks,
        "similar_open_prs": similar,
        "active_attempts": _safe_attempts(bounties.get(bounty_ref, {})) if bounty_ref else [],
    }


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


def _load_issue_maintainer_activity(repo: str, issue_number: int) -> dict[str, Any]:
    issue = _run_gh_json(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            repo,
            "--json",
            "author,comments,createdAt",
        ]
    )
    activity_times = []
    repo_owner = repo.split("/", 1)[0].lower()
    issue_author = str((issue.get("author") or {}).get("login") or "").lower()
    created_at = _parse_datetime(issue.get("createdAt"))
    if issue_author == repo_owner and created_at is not None:
        activity_times.append(created_at)
    for comment in issue.get("comments") or []:
        if str(comment.get("authorAssociation") or "").upper() not in MAINTAINER_ASSOCIATIONS:
            continue
        created_at = _parse_datetime(comment.get("createdAt"))
        if created_at is not None:
            activity_times.append(created_at)
    if not activity_times:
        return {"maintainer_activity_verified": False}
    return {
        "maintainer_activity_verified": True,
        "last_maintainer_activity_at": _isoformat_utc(max(activity_times)),
    }


def _load_api_bounties(repo: str, api_host: str) -> dict[int, dict[str, Any]]:
    url = f"{api_host.rstrip('/')}/api/v1/bounties?status=open"
    try:
        with urlopen(url, timeout=GH_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"MergeWork API bounty data unavailable: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("MergeWork API bounty data must be a list")
    bounties: dict[int, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict) or item.get("repo") != repo:
            continue
        issue_number = item.get("issue_number")
        if not isinstance(issue_number, int):
            continue
        bounties[issue_number] = {
            "id": item.get("id"),
            "number": issue_number,
            "state": item.get("status", "open"),
            "awards_remaining": item.get("awards_remaining"),
        }
    return bounties


def _normalize_attempt(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "submitter": _attempt_field(
            raw, "submitter", "submitter_account", "account", "github_login"
        ),
        "source_url": _attempt_field(raw, "source_url", "public_source_url", "url"),
        "status": _attempt_field(raw, "status"),
        "expires_at": _attempt_field(raw, "expires_at", "expiresAt", "expiry_time"),
    }


def _load_api_attempts(api_host: str, bounty_id: Any) -> list[dict[str, Any]]:
    if not isinstance(bounty_id, int):
        raise RuntimeError("MergeWork API bounty id unavailable for attempts lookup")
    url = f"{api_host.rstrip('/')}/api/v1/bounties/{bounty_id}/attempts"
    try:
        with urlopen(url, timeout=GH_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"MergeWork API attempts data unavailable: {exc}") from exc
    attempts = payload.get("attempts") if isinstance(payload, dict) else payload
    if not isinstance(attempts, list):
        raise RuntimeError("MergeWork API attempts data must be a list")
    return [_normalize_attempt(attempt) for attempt in attempts if isinstance(attempt, dict)]


def _load_live_context(
    repo: str,
    submission_text: str,
    api_host: str,
    max_maintainer_age_days: int = DEFAULT_MAX_MAINTAINER_AGE_DAYS,
) -> dict[str, Any]:
    load_warnings: list[str] = []
    try:
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
                "number,title,url,body,state",
            ]
        )
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
                "number,title,state",
            ]
        )
    except (RuntimeError, FileNotFoundError, json.JSONDecodeError) as exc:
        return {
            "submission_text": submission_text,
            "bounties": [],
            "pull_requests": [],
            "load_warning": f"live GitHub data unavailable: {exc}",
        }
    if len(prs) >= GH_PR_SAFETY_CAP:
        load_warnings.append(
            f"gh pr list reached the {GH_PR_SAFETY_CAP} item safety cap; "
            "similar-open-PR checks may be incomplete"
        )
    if len(issues) >= GH_ISSUE_SAFETY_CAP:
        load_warnings.append(
            f"gh issue list reached the {GH_ISSUE_SAFETY_CAP} item safety cap; "
            "bounty discovery may be incomplete"
        )
    try:
        api_bounties = _load_api_bounties(repo, api_host)
    except RuntimeError as exc:
        api_bounties = {}
        load_warnings.append(str(exc))
    referenced_bounties = set(_bounty_refs(submission_text))
    bounties = []
    for issue in issues:
        if "bounty" not in str(issue.get("title", "")).lower():
            continue
        api_bounty = api_bounties.get(issue["number"], {})
        awards_remaining = api_bounty.get("awards_remaining")
        bounties.append(
            {
                "id": api_bounty.get("id"),
                "number": issue["number"],
                "title": issue.get("title"),
                "state": issue.get("state"),
                "awards_remaining": awards_remaining,
                "payability_verified": issue["number"] in api_bounties
                and awards_remaining is not None,
            }
        )
        if issue["number"] in referenced_bounties:
            try:
                bounties[-1].update(_load_issue_maintainer_activity(repo, issue["number"]))
                bounties[-1]["max_maintainer_age_days"] = max_maintainer_age_days
            except (RuntimeError, FileNotFoundError, json.JSONDecodeError) as exc:
                bounties[-1]["maintainer_activity_verified"] = False
                load_warnings.append(
                    f"maintainer activity unavailable for bounty #{issue['number']}: {exc}"
                )
            bounty_id = api_bounty.get("id")
            if isinstance(bounty_id, int):
                try:
                    bounties[-1]["active_attempts"] = _load_api_attempts(api_host, bounty_id)
                    bounties[-1]["active_attempts_verified"] = True
                except RuntimeError as exc:
                    bounties[-1]["active_attempts"] = []
                    bounties[-1]["active_attempts_verified"] = False
                    load_warnings.append(
                        f"active attempts unavailable for bounty #{issue['number']}: {exc}"
                    )
            else:
                bounties[-1]["active_attempts"] = []
                bounties[-1]["active_attempts_verified"] = False
                load_warnings.append(
                    f"active attempts unavailable for bounty #{issue['number']}: "
                    "MergeWork API bounty id unavailable for attempts lookup"
                )
    data = {"submission_text": submission_text, "bounties": bounties, "pull_requests": prs}
    if load_warnings:
        data["load_warning"] = "; ".join(load_warnings)
    return data


def _load_input(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("quality gate input must be a JSON object")
    return data


def format_text(result: dict[str, Any]) -> str:
    lines = [f"Submission quality gate: {result['status'].upper()}"]
    if result.get("load_warning"):
        lines.append(f"Warning: {result['load_warning']}")
    if result.get("bounty_reference") is not None:
        lines.append(f"Bounty reference: #{result['bounty_reference']}")
    for check in result["checks"]:
        lines.append(f"- {check['status'].upper()} {check['name']}: {check['message']}")
    if result["similar_open_prs"]:
        lines.append("Similar open PRs:")
        for pr in result["similar_open_prs"]:
            lines.append(f"- #{pr['number']}: {pr['title']} {pr.get('url') or ''}".rstrip())
    if result.get("active_attempts"):
        lines.append("Active attempts:")
        for attempt in result["active_attempts"]:
            lines.append(f"- {_format_attempt_summary(attempt)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a MergeWork bounty submission draft.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Read gate input from a JSON fixture file.")
    source.add_argument("--text-file", help="Read submission text and live context with gh.")
    parser.add_argument("--repo", default="ramimbo/mergework")
    parser.add_argument("--api-host", default=DEFAULT_API_HOST)
    parser.add_argument(
        "--max-maintainer-age-days",
        type=int,
        default=DEFAULT_MAX_MAINTAINER_AGE_DAYS,
        help="Warn when the referenced bounty has no maintainer activity within this many days.",
    )
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args(argv)

    if args.input:
        data = _load_input(args.input)
    else:
        with open(args.text_file, encoding="utf-8") as handle:
            data = _load_live_context(
                args.repo,
                handle.read(),
                args.api_host,
                args.max_maintainer_age_days,
            )
    result = evaluate_submission(data)
    if data.get("load_warning"):
        result["load_warning"] = data["load_warning"]

    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_text(result))
    return 1 if result["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
