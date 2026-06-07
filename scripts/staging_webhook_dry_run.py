from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import httpx

from scripts.api_host_args import public_http_url

_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _post_json(url: str, payload: dict[str, object], headers: dict[str, str]) -> dict[str, object]:
    _validate_http_url(url)
    return _parse_object_response(url, httpx.post(url, json=payload, headers=headers, timeout=20))


def _post_webhook(
    url: str, payload: dict[str, object], secret: str, delivery_id: str
) -> dict[str, object]:
    _validate_http_url(url)
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return _parse_object_response(
        url,
        httpx.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Delivery": delivery_id,
                "X-GitHub-Event": "issues",
                "X-Hub-Signature-256": f"sha256={signature}",
            },
            timeout=20,
        ),
    )


def _parse_object_response(url: str, response: httpx.Response) -> dict[str, object]:
    response.raise_for_status()
    parsed = response.json()
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{url} returned a non-object JSON payload")
    return parsed


def _validate_http_url(url: str) -> None:
    try:
        public_http_url(url, label="staging dry-run URL", forbid_credentials=True)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from None


def _dry_run_repo() -> str:
    repo = os.environ.get("MERGEWORK_DRY_RUN_REPO", "ramimbo/mergework").strip()
    if not repo:
        raise RuntimeError("MERGEWORK_DRY_RUN_REPO is required")
    if not _REPO_SLUG_RE.fullmatch(repo):
        raise RuntimeError("MERGEWORK_DRY_RUN_REPO must be a GitHub repo slug in owner/name form")
    return repo


def _dry_run_contributor() -> str:
    contributor = os.environ.get("MERGEWORK_DRY_RUN_CONTRIBUTOR", "mergework-dry-run").strip()
    if not contributor:
        raise RuntimeError("MERGEWORK_DRY_RUN_CONTRIBUTOR is required")
    return contributor


def _enforce_staging_target(base_url: str) -> None:
    _validate_http_url(base_url)
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if is_loopback or "staging" in host:
        return
    if os.environ.get("MERGEWORK_ALLOW_NON_STAGING_DRY_RUN") == "1":
        return
    raise RuntimeError(
        "MERGEWORK_STAGING_BASE_URL must point at localhost or a host containing "
        "'staging'. Set MERGEWORK_ALLOW_NON_STAGING_DRY_RUN=1 only for an intentional target."
    )


def main() -> int:
    try:
        base_url = _required_env("MERGEWORK_STAGING_BASE_URL").rstrip("/")
        _enforce_staging_target(base_url)
        admin_token = _required_env("MERGEWORK_ADMIN_TOKEN")
        webhook_secret = _required_env("MERGEWORK_GITHUB_WEBHOOK_SECRET")
        repo = _dry_run_repo()
        contributor = _dry_run_contributor()
        labeler = _required_env("MERGEWORK_GITHUB_ACCEPTED_LABELERS").split(",", 1)[0].strip()
        issue_number = int(time.time() * 1000)
        issue_url = f"https://github.com/{repo}/issues/{issue_number}"
        bounty = _post_json(
            f"{base_url}/api/v1/bounties",
            {
                "repo": repo,
                "issue_number": issue_number,
                "issue_url": issue_url,
                "title": f"Staging webhook dry run {issue_number}",
                "reward_mrwk": "0.000001",
                "acceptance": "Staging dry run only.",
            },
            {"X-MergeWork-Admin-Token": admin_token},
        )
        delivery_id = f"mergework-dry-run-{issue_number}"
        webhook = _post_webhook(
            f"{base_url}/webhooks/github",
            {
                "action": "labeled",
                "label": {"name": "mrwk:accepted"},
                "issue": {
                    "number": issue_number,
                    "html_url": issue_url,
                    "user": {"login": contributor},
                },
                "repository": {"full_name": repo},
                "sender": {"login": labeler},
            },
            webhook_secret,
            delivery_id,
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        httpx.HTTPError,
    ) as exc:
        print(f"Staging webhook dry run failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"bounty": bounty, "webhook": webhook}, indent=2, sort_keys=True))
    if webhook.get("status") != "paid":
        print("Staging webhook dry run did not produce a paid webhook result.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
