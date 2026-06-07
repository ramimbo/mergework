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
from scripts.bounty_refs import GITHUB_CLOSING_ISSUE_RE

DEFAULT_API_HOST = "https://api.mrwk.online"
GH_TIMEOUT_SECONDS = 30
GH_PR_SAFETY_CAP = 200
MAX_BOUNTY_REF = 2**63 - 1


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _status_value(raw: dict[str, Any]) -> str:
    return str(raw.get("status") or raw.get("state") or "").lower()


def _issue_number(raw: dict[str, Any]) -> int | None:
    return _int_or_none(raw.get("issue_number", raw.get("number")))


def _open_public_bounty_numbers(data: dict[str, Any]) -> set[int]:
    numbers: set[int] = set()
    for item in data.get("bounties", []):
        if not isinstance(item, dict) or _status_value(item) != "open":
            continue
        issue_number = _issue_number(item)
        if issue_number is not None:
            numbers.add(issue_number)
    return numbers


def _closing_refs(text: str) -> list[tuple[int, str]]:
    refs: list[tuple[int, str]] = []
    for match in GITHUB_CLOSING_ISSUE_RE.finditer(text or ""):
        issue_number = _int_or_none(match.group("issue"))
        if issue_number is None or issue_number > MAX_BOUNTY_REF:
            continue
        refs.append((issue_number, f"{match.group('verb')} #{issue_number}"))
    return refs


def analyze_closing_refs(data: dict[str, Any]) -> dict[str, Any]:
    open_bounties = _open_public_bounty_numbers(data)
    violations: list[dict[str, Any]] = []
    pull_requests = [item for item in data.get("pull_requests", []) if isinstance(item, dict)]
    for pr in pull_requests:
        number = _int_or_none(pr.get("number"))
        if number is None:
            continue
        text = "\n".join(str(pr.get(key) or "") for key in ("title", "body"))
        for issue_number, matched_reference in _closing_refs(text):
            if issue_number not in open_bounties:
                continue
            violations.append(
                {
                    "pull_request": number,
                    "title": str(pr.get("title") or ""),
                    "url": pr.get("url"),
                    "issue_number": issue_number,
                    "matched_reference": matched_reference,
                    "detail": (
                        f"PR #{number} uses closing reference {matched_reference!r} "
                        f"against open public bounty #{issue_number}"
                    ),
                }
            )
    return {
        "summary": {
            "pull_requests": len(pull_requests),
            "open_public_bounties": len(open_bounties),
            "closing_references_to_open_bounties": len(violations),
        },
        "violations": violations,
    }


def has_violations(report: dict[str, Any]) -> bool:
    return bool(report["violations"])


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def format_text_report(report: dict[str, Any]) -> str:
    lines = ["Live bounty closing-reference check"]
    for key, value in report["summary"].items():
        lines.append(f"- {key.replace('_', ' ')}: {value}")
    if not has_violations(report):
        lines.append("")
        lines.append("No closing references to open public bounties found.")
        return "\n".join(lines)
    lines.append("")
    lines.append("Closing references to open public bounties")
    for item in report["violations"]:
        lines.append(
            "- PR #{pull_request}: {title} ({matched_reference} -> bounty #{issue_number})".format(
                pull_request=item["pull_request"],
                title=_single_line(item["title"]),
                matched_reference=item["matched_reference"],
                issue_number=item["issue_number"],
            )
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
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "gh command failed "
            f"(exit {exc.returncode}): {command}\n"
            f"stdout:\n{exc.stdout or exc.output or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        ) from exc
    return json.loads(completed.stdout)


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


def _load_pull_requests(repo: str, state: str, pr_numbers: list[int]) -> list[dict[str, Any]]:
    if pr_numbers:
        return [
            _run_gh_json(
                [
                    "gh",
                    "pr",
                    "view",
                    str(number),
                    "--repo",
                    repo,
                    "--json",
                    "number,title,url,body,state",
                ]
            )
            for number in pr_numbers
        ]
    prs = _run_gh_json(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            state,
            "--limit",
            str(GH_PR_SAFETY_CAP),
            "--json",
            "number,title,url,body,state",
        ]
    )
    if len(prs) >= GH_PR_SAFETY_CAP:
        raise RuntimeError(
            f"gh pr list reached the {GH_PR_SAFETY_CAP} item safety cap; "
            "use --pr for a bounded check or an API-paginated collector"
        )
    return [item for item in prs if isinstance(item, dict)]


def load_live_data(repo: str, api_host: str, state: str, pr_numbers: list[int]) -> dict[str, Any]:
    return {
        "bounties": _load_public_bounties(api_host),
        "pull_requests": _load_pull_requests(repo, state, pr_numbers),
    }


def _load_input(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail when GitHub closing keywords target currently open public MergeWork "
            "bounty issues."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Read bounties and pull_requests from a JSON fixture.")
    source.add_argument("--repo", help="GitHub repository, for example ramimbo/mergework.")
    parser.add_argument("--api-host", type=public_api_host, default=DEFAULT_API_HOST)
    parser.add_argument("--state", choices=["open", "closed", "merged", "all"], default="open")
    parser.add_argument("--pr", type=int, action="append", default=[], help="Specific PR to check.")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    parser.add_argument("--fail-on-issues", action="store_true")
    args = parser.parse_args(argv)

    data = (
        _load_input(args.input)
        if args.input
        else load_live_data(args.repo, args.api_host, args.state, args.pr)
    )
    report = analyze_closing_refs(data)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_text_report(report))
    return 1 if args.fail_on_issues and has_violations(report) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
