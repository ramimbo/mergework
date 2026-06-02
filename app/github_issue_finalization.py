from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

GITHUB_LABEL = "mrwk:bounty"
GITHUB_PAID_LABEL = "mrwk:paid"
USER_AGENT = "MergeWork"
API_VERSION = "2022-11-28"
LIVE_BOUNTY_STATUS_BLOCK_START = "<!-- mergework:mrwk:live-bounty-status:start -->"
LIVE_BOUNTY_STATUS_BLOCK_END = "<!-- mergework:mrwk:live-bounty-status:end -->"
STALE_PENDING_BOUNTY_STATUS_TEXT = (
    (
        "Status: proposed bounty. This issue is not claimable until the treasury proposal "
        "executes, the public bounty page exists, and the `Reserved on MergeWork` "
        "claims-open comment is posted."
    ),
    "Status: proposed bounty. This issue is not claimable yet.",
)
PAID_BOUNTY_FINALIZATION_COMMENT_MARKER = "<!-- mergework:mrwk:paid-bounty-finalized -->"


def _github_request(
    url: str,
    github_token: str,
    payload: dict[str, Any] | None = None,
    *,
    method: str = "POST",
) -> Request:
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": API_VERSION,
    }
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    return Request(
        url,
        data=data,
        method=method,
        headers=headers,
    )


def _read_json_value(response: Any) -> Any:
    body = response.read()
    if not body:
        return {}
    return json.loads(body.decode())


def _read_json(response: Any) -> dict[str, Any]:
    data = _read_json_value(response)
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _post_json(
    *,
    opener: Callable[..., Any],
    url: str,
    github_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    request = _github_request(url, github_token, payload)
    with opener(request, timeout=10) as response:
        return _read_json(response)


def _get_json(
    *,
    opener: Callable[..., Any],
    url: str,
    github_token: str,
) -> dict[str, Any]:
    request = _github_request(url, github_token, method="GET")
    with opener(request, timeout=10) as response:
        return _read_json(response)


def _get_json_list(
    *,
    opener: Callable[..., Any],
    url: str,
    github_token: str,
) -> list[dict[str, Any]]:
    request = _github_request(url, github_token, method="GET")
    with opener(request, timeout=10) as response:
        data = _read_json_value(response)
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _patch_json(
    *,
    opener: Callable[..., Any],
    url: str,
    github_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    request = _github_request(url, github_token, payload, method="PATCH")
    with opener(request, timeout=10) as response:
        return _read_json(response)


def _bounty_issue_target(bounty: dict[str, object]) -> tuple[str, int, int] | None:
    try:
        bounty_id = int(str(bounty.get("id", "")))
        issue_number = int(str(bounty.get("issue_number", "")))
    except (TypeError, ValueError):
        return None
    repo = str(bounty.get("repo", "")).strip().lower()
    repo_parts = repo.split("/")
    if bounty_id <= 0 or issue_number <= 0 or len(repo_parts) != 2 or not all(repo_parts):
        return None
    return repo, issue_number, bounty_id


def _github_issue_api_base(repo: str, issue_number: int) -> str:
    owner_part, name_part = repo.split("/", 1)
    owner = quote(owner_part, safe="")
    name = quote(name_part, safe="")
    return f"https://api.github.com/repos/{owner}/{name}/issues/{issue_number}"


def _has_label(issue: dict[str, Any], label: str) -> bool:
    labels = issue.get("labels")
    if not isinstance(labels, list):
        return False
    for item in labels:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name.lower() == label.lower():
            return True
    return False


def _paid_finalization_comment_body(bounty_url: str) -> str:
    return (
        f"Filled and paid on MergeWork: {bounty_url}\n\n"
        "All awards for this bounty have proof-backed payments, "
        "so this bounty issue is closed.\n\n"
        f"{PAID_BOUNTY_FINALIZATION_COMMENT_MARKER}"
    )


def _existing_paid_finalization_comment_url(comments: list[dict[str, Any]]) -> str | None:
    for comment in comments:
        body = comment.get("body")
        if not isinstance(body, str) or PAID_BOUNTY_FINALIZATION_COMMENT_MARKER not in body:
            continue
        html_url = comment.get("html_url")
        return str(html_url) if html_url is not None else None
    return None


def _live_bounty_status_block(bounty_url: str) -> str:
    return "\n".join(
        [
            LIVE_BOUNTY_STATUS_BLOCK_START,
            "**MergeWork status:** live bounty",
            "",
            f"Reserved on MergeWork: {bounty_url}",
            "",
            (
                "Claims are open only while the public bounty row remains open "
                "and effective award capacity remains. Use the public bounty "
                "page or API as the authoritative current status."
            ),
            LIVE_BOUNTY_STATUS_BLOCK_END,
        ]
    )


def _without_stale_pending_bounty_status_text(body: str) -> str:
    paragraphs = body.split("\n\n")
    filtered = [
        paragraph
        for paragraph in paragraphs
        if paragraph.strip() not in STALE_PENDING_BOUNTY_STATUS_TEXT
    ]
    return "\n\n".join(filtered).strip("\n")


def _issue_body_with_live_bounty_status(body: str, bounty_url: str) -> str:
    block = _live_bounty_status_block(bounty_url)
    start = body.find(LIVE_BOUNTY_STATUS_BLOCK_START)
    end = body.find(LIVE_BOUNTY_STATUS_BLOCK_END)
    if start != -1 and end != -1 and start < end:
        end += len(LIVE_BOUNTY_STATUS_BLOCK_END)
        updated = f"{body[:start]}{block}{body[end:]}"
    else:
        updated = f"{block}\n\n{body}" if body else block
    return _without_stale_pending_bounty_status_text(updated)


def _refresh_live_bounty_issue_body(
    *,
    opener: Callable[..., Any],
    issue_api_base: str,
    github_token: str,
    bounty_url: str,
) -> dict[str, str]:
    issue = _get_json(opener=opener, url=issue_api_base, github_token=github_token)
    body = issue.get("body")
    clean_body = body if isinstance(body, str) else ""
    updated_body = _issue_body_with_live_bounty_status(clean_body, bounty_url)
    if updated_body == clean_body:
        return {"status": "already_current"}
    _patch_json(
        opener=opener,
        url=issue_api_base,
        github_token=github_token,
        payload={"body": updated_body},
    )
    return {"status": "updated"}


def finalize_created_bounty_issue(
    *,
    github_token: str,
    public_base_url: str,
    bounty: dict[str, object],
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    clean_token = github_token.strip()
    if not clean_token:
        return {"status": "skipped", "reason": "github issue token not configured"}
    target = _bounty_issue_target(bounty)
    if target is None:
        return {"status": "failed", "reason": "bounty issue target missing or invalid"}
    repo, issue_number, bounty_id = target
    issue_api_base = _github_issue_api_base(repo, issue_number)
    bounty_url = f"{public_base_url.rstrip('/')}/bounties/{bounty_id}"
    comment = (
        f"Reserved on MergeWork: {bounty_url}\n\n"
        "Claims are now open for accepted work that matches this bounty's criteria."
    )
    try:
        _post_json(
            opener=opener,
            url=f"{issue_api_base}/labels",
            github_token=clean_token,
            payload={"labels": [GITHUB_LABEL]},
        )
        comment_response = _post_json(
            opener=opener,
            url=f"{issue_api_base}/comments",
            github_token=clean_token,
            payload={"body": comment},
        )
        try:
            body_update = _refresh_live_bounty_issue_body(
                opener=opener,
                issue_api_base=issue_api_base,
                github_token=clean_token,
                bounty_url=bounty_url,
            )
        except HTTPError as exc:
            body_update = {
                "status": "failed",
                "reason": f"github issue body update failed: HTTP {exc.code}",
            }
        except (OSError, ValueError) as exc:
            body_update = {
                "status": "failed",
                "reason": f"github issue body update failed: {type(exc).__name__}",
            }
    except HTTPError as exc:
        return {"status": "failed", "reason": f"github issue update failed: HTTP {exc.code}"}
    except (OSError, ValueError) as exc:
        return {"status": "failed", "reason": f"github issue update failed: {type(exc).__name__}"}
    result = {
        "status": "updated",
        "label": GITHUB_LABEL,
        "bounty_url": bounty_url,
        "comment_url": comment_response.get("html_url"),
        "issue_body_status": body_update["status"],
    }
    if "reason" in body_update:
        result["issue_body_reason"] = body_update["reason"]
    return result


def finalize_paid_bounty_issue(
    *,
    github_token: str,
    public_base_url: str,
    bounty: dict[str, object],
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    clean_token = github_token.strip()
    if not clean_token:
        return {"status": "skipped", "reason": "github issue token not configured"}
    target = _bounty_issue_target(bounty)
    if target is None:
        return {"status": "failed", "reason": "bounty issue target missing or invalid"}
    repo, issue_number, bounty_id = target
    issue_api_base = _github_issue_api_base(repo, issue_number)
    bounty_url = f"{public_base_url.rstrip('/')}/bounties/{bounty_id}"
    try:
        issue = _get_json(opener=opener, url=issue_api_base, github_token=clean_token)
        issue_state = str(issue.get("state") or "").lower()
        has_paid_label = _has_label(issue, GITHUB_PAID_LABEL)
        if issue_state == "closed" and has_paid_label:
            return {
                "status": "already_finalized",
                "label": GITHUB_PAID_LABEL,
                "bounty_url": bounty_url,
                "closed": True,
            }
        if not has_paid_label:
            _post_json(
                opener=opener,
                url=f"{issue_api_base}/labels",
                github_token=clean_token,
                payload={"labels": [GITHUB_PAID_LABEL]},
            )
        comment_url = None
        if issue_state != "closed":
            comments = _get_json_list(
                opener=opener,
                url=f"{issue_api_base}/comments?per_page=100",
                github_token=clean_token,
            )
            comment_url = _existing_paid_finalization_comment_url(comments)
            if comment_url is None:
                comment_response = _post_json(
                    opener=opener,
                    url=f"{issue_api_base}/comments",
                    github_token=clean_token,
                    payload={"body": _paid_finalization_comment_body(bounty_url)},
                )
                comment_url = comment_response.get("html_url")
        if issue_state != "closed":
            _patch_json(
                opener=opener,
                url=issue_api_base,
                github_token=clean_token,
                payload={"state": "closed", "state_reason": "completed"},
            )
        result = {
            "status": "updated",
            "label": GITHUB_PAID_LABEL,
            "bounty_url": bounty_url,
            "closed": True,
        }
        if comment_url is not None:
            result["comment_url"] = comment_url
        return result
    except HTTPError as exc:
        return {"status": "failed", "reason": f"github issue update failed: HTTP {exc.code}"}
    except (OSError, ValueError) as exc:
        return {"status": "failed", "reason": f"github issue update failed: {type(exc).__name__}"}
