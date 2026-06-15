from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.api_host_args import public_api_host

DEFAULT_API_HOST = "https://api.mrwk.online"
GH_TIMEOUT_SECONDS = 30


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _status_value(raw: dict[str, Any]) -> str:
    return str(raw.get("status") or raw.get("state") or "").lower()


def _issue_number(raw: dict[str, Any]) -> int | None:
    return _int_or_none(raw.get("issue_number", raw.get("number")))


def _open_public_bounties(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in data.get("bounties", []):
        if not isinstance(item, dict) or _status_value(item) != "open":
            continue
        issue_number = _issue_number(item)
        if issue_number is None:
            continue
        rows.append({**item, "issue_number": issue_number})
    return rows


def _issue_index(data: dict[str, Any]) -> dict[int, dict[str, Any]]:
    index: dict[int, dict[str, Any]] = {}
    for issue in data.get("issues", []):
        if not isinstance(issue, dict):
            continue
        number = _issue_number(issue)
        if number is not None:
            index[number] = issue
    return index


def _issue_is_open(issue: dict[str, Any] | None) -> bool:
    if issue is None:
        return False
    return _status_value(issue) == "open"


def analyze_issue_states(data: dict[str, Any]) -> dict[str, Any]:
    open_bounties = _open_public_bounties(data)
    issues = _issue_index(data)
    violations: list[dict[str, Any]] = []
    for bounty in open_bounties:
        issue_number = int(bounty["issue_number"])
        issue = issues.get(issue_number)
        if _issue_is_open(issue):
            continue
        issue_state = _status_value(issue or {}) or "missing"
        violations.append(
            {
                "issue_number": issue_number,
                "bounty_id": _int_or_none(bounty.get("id", bounty.get("bounty_id"))),
                "availability_state": bounty.get("availability_state"),
                "effective_awards_remaining": bounty.get("effective_awards_remaining"),
                "awards_paid": bounty.get("awards_paid"),
                "max_awards": bounty.get("max_awards"),
                "issue_state": issue_state,
                "issue_url": bounty.get("issue_url") or (issue or {}).get("url"),
                "detail": (
                    f"Open public bounty #{issue_number} has GitHub issue state "
                    f"{issue_state.upper()}"
                ),
            }
        )
    return {
        "summary": {
            "open_public_bounties": len(open_bounties),
            "closed_or_missing_github_issues": len(violations),
        },
        "violations": violations,
    }


def has_violations(report: dict[str, Any]) -> bool:
    return bool(report["violations"])


def format_text_report(report: dict[str, Any]) -> str:
    lines = ["Public bounty GitHub issue-state check"]
    for key, value in report["summary"].items():
        lines.append(f"- {key.replace('_', ' ')}: {value}")
    if not has_violations(report):
        lines.append("")
        lines.append("All open public bounty rows have open GitHub issues.")
        return "\n".join(lines)
    lines.append("")
    lines.append("Open public bounty rows with non-open GitHub issues")
    for item in report["violations"]:
        detail = item["detail"]
        if item.get("effective_awards_remaining") is not None:
            detail += f"; effective awards remaining: {item['effective_awards_remaining']}"
        lines.append(f"- #{item['issue_number']}: {detail}")
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


def _run_gh(args: list[str]) -> None:
    command = " ".join(args)
    try:
        subprocess.run(
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


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=GH_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to fetch JSON from {url}: {exc}") from exc


def _load_public_bounties(api_host: str) -> list[dict[str, Any]]:
    url = f"{api_host.rstrip('/')}/api/v1/bounties?status=open&limit=200"
    data = _fetch_json(url)
    if not isinstance(data, list):
        raise RuntimeError(f"expected a JSON list from {url}")
    return [item for item in data if isinstance(item, dict)]


def _load_issue(repo: str, issue_number: int) -> dict[str, Any]:
    return _run_gh_json(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            repo,
            "--json",
            "number,state,url,closed,closedAt",
        ]
    )


def load_live_data(repo: str, api_host: str) -> dict[str, Any]:
    bounties = _load_public_bounties(api_host)
    issue_numbers = sorted(
        number for number in (_issue_number(bounty) for bounty in bounties) if number is not None
    )
    return {
        "bounties": bounties,
        "issues": [_load_issue(repo, issue_number) for issue_number in issue_numbers],
    }


def reopen_violations(repo: str, violations: list[dict[str, Any]]) -> None:
    for item in violations:
        if item.get("issue_state") == "closed":
            _run_gh(["gh", "issue", "reopen", str(item["issue_number"]), "--repo", repo])


def _load_input(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify open public MergeWork bounty rows still have open GitHub issues."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Read bounties and issues from a JSON fixture.")
    source.add_argument("--repo", help="GitHub repository, for example ramimbo/mergework.")
    parser.add_argument("--api-host", type=public_api_host, default=DEFAULT_API_HOST)
    parser.add_argument("--format", choices=["json", "text"], default="text")
    parser.add_argument("--fail-on-issues", action="store_true")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Reopen closed GitHub issues for open public bounties. Does not post comments.",
    )
    args = parser.parse_args(argv)

    data = _load_input(args.input) if args.input else load_live_data(args.repo, args.api_host)
    report = analyze_issue_states(data)
    if args.fix:
        if args.input:
            raise SystemExit("--fix requires --repo, not --input")
        reopen_violations(args.repo, report["violations"])
        data = load_live_data(args.repo, args.api_host)
        report = analyze_issue_states(data)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_text_report(report))
    return 1 if args.fail_on_issues and has_violations(report) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
