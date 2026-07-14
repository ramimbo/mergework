from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.public_payment_language import (
    find_payment_language_violations,
    format_violation_report,
)

DEFAULT_TIMEOUT_SECONDS = 30


def _github_token() -> str:
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise RuntimeError("GitHub token required; set GH_TOKEN or GITHUB_TOKEN")


def _load_pull_request(repo: str, number: int) -> dict[str, Any]:
    owner, name = repo.split("/", 1)
    url = f"https://api.github.com/repos/{owner}/{name}/pulls/{number}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {_github_token()}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "mergework-pr-payment-language-check",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to fetch PR #{number} from GitHub API: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"GitHub API returned non-object JSON for PR #{number}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a PR body uses premature payment/status wording."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text-file", help="Read submission/PR body text from a file.")
    source.add_argument("--repo", help="GitHub repository, for example ramimbo/mergework.")
    parser.add_argument("--pr", type=int, help="Pull request number (required with --repo).")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    parser.add_argument("--fail-on-issues", action="store_true")
    args = parser.parse_args(argv)

    if args.repo and args.pr is None:
        parser.error("--pr is required when using --repo")
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
        context = {"source": "text_file", "pull_request": None}
    else:
        assert args.repo is not None and args.pr is not None
        pr = _load_pull_request(args.repo, args.pr)
        text = "\n".join(str(pr.get(key) or "") for key in ("title", "body"))
        context = {
            "source": "github_api",
            "pull_request": args.pr,
            "url": pr.get("html_url"),
        }

    violations = find_payment_language_violations(text)
    report = {"context": context, "violations": violations}
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_violation_report(violations))
    return 1 if args.fail_on_issues and violations else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
