from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.control_chars import contains_control_character
from app.db import session_scope
from app.ledger.service import (
    LedgerError,
    find_bounty_by_issue,
    pay_bounty,
    resolve_payout_account,
)
from app.models import Bounty, WebhookEvent

ACCEPTED_LABEL = "mrwk:accepted"
PROPOSED_WORK_LABEL = "proposed-work"
PROPOSED_WORK_ACTIONS = {"opened", "edited", "reopened"}
ISSUE_NUMBER_BOUNDARY = r"(?![A-Za-z0-9_-])"
MAX_SQLITE_INTEGER = 2**63 - 1
LINKED_ISSUE_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|references?|bounty|claims?)"
    r"(?:\s+|\s*:\s*)"
    rf"(?:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(?P<repo_number>\d+){ISSUE_NUMBER_BOUNDARY}"
    rf"|#(?P<number>\d+){ISSUE_NUMBER_BOUNDARY})",
    re.IGNORECASE,
)
GITHUB_ISSUE_URL_RE = re.compile(
    rf"https://github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(?P<number>\d+){ISSUE_NUMBER_BOUNDARY}",
    re.IGNORECASE,
)
PROPOSED_WORK_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<heading>.+?)\s*#*\s*$", re.MULTILINE)
PROPOSED_WORK_REQUIRED_HEADINGS = (
    ("problem",),
    ("expected value", "value"),
    ("duplicate search",),
    ("out of scope",),
)
PROPOSED_WORK_HEADING_ALTERNATES = (
    ("evidence", "current evidence"),
    ("proposed work", "proposed scope"),
    ("acceptance", "verification", "possible acceptance criteria"),
)


def verify_github_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header, f"sha256={expected}")


def _payload_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _record_event(
    database_url: str,
    delivery_id: str,
    event_type: str,
    payload_hash: str,
    status: str,
) -> None:
    with session_scope(database_url) as session:
        session.add(
            WebhookEvent(
                delivery_id=delivery_id,
                event_type=event_type,
                payload_hash=payload_hash,
                processed_status=status,
            )
        )


def _linked_issue_numbers(body: str, current_repo: str) -> list[int]:
    numbers: list[int] = []
    for match in LINKED_ISSUE_RE.finditer(body):
        repo = match.group("repo")
        if repo is not None and repo.lower() != current_repo.lower():
            continue
        number = match.group("repo_number") or match.group("number")
        if number is not None:
            issue_number = _github_issue_number_from_text(number)
            if issue_number is not None and issue_number not in numbers:
                numbers.append(issue_number)
    for match in GITHUB_ISSUE_URL_RE.finditer(body):
        if match.group("repo").lower() != current_repo.lower():
            continue
        number = _github_issue_number_from_text(match.group("number"))
        if number is not None and number not in numbers:
            numbers.append(number)
    return numbers


def _record_status(
    database_url: str,
    delivery_id: str,
    event_type: str,
    payload_hash: str,
    status: str,
) -> dict[str, Any]:
    _record_event(database_url, delivery_id, event_type, payload_hash, status)
    return {"status": status}


def _payload_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key) or {}
    return value if isinstance(value, dict) else {}


def _clean_webhook_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if contains_control_character(value):
        return None
    clean = value.strip()
    return clean or None


def _github_issue_number_from_text(value: str) -> int | None:
    normalized = value.lstrip("0") or "0"
    if len(normalized) > len(str(MAX_SQLITE_INTEGER)):
        return None
    issue_number = int(normalized)
    if issue_number <= 0 or issue_number > MAX_SQLITE_INTEGER:
        return None
    return issue_number


def _github_issue_number(value: Any) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > MAX_SQLITE_INTEGER
    ):
        return None
    return value


def _github_issue_api_base(repo: str, issue_number: int) -> str:
    owner_part, name_part = repo.split("/", 1)
    owner = quote(owner_part, safe="")
    name = quote(name_part, safe="")
    return f"https://api.github.com/repos/{owner}/{name}/issues/{issue_number}"


def _github_request(url: str, github_token: str, payload: dict[str, Any]) -> Request:
    return Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode(),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "Content-Type": "application/json",
            "User-Agent": "MergeWork",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _post_github_json(
    *,
    opener: Callable[..., Any],
    url: str,
    github_token: str,
    payload: dict[str, Any],
) -> None:
    request = _github_request(url, github_token, payload)
    with opener(request, timeout=10) as response:
        response.read()


def _issue_has_label(issue: dict[str, Any], label: str) -> bool:
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


def _normalized_markdown_headings(body: str) -> tuple[str, ...]:
    headings: list[str] = []
    for match in PROPOSED_WORK_HEADING_RE.finditer(body):
        heading = match.group("heading").rstrip("#").strip()
        normalized = re.sub(r"\s+", " ", heading).casefold().strip(" :")
        if normalized:
            headings.append(normalized)
    return tuple(headings)


def _has_markdown_heading(headings: tuple[str, ...], alternatives: tuple[str, ...]) -> bool:
    for heading in headings:
        for alternative in alternatives:
            if heading == alternative or heading.startswith(f"{alternative} "):
                return True
    return False


def _looks_like_proposed_work_issue(issue: dict[str, Any]) -> bool:
    title = _clean_webhook_string(issue.get("title"))
    if title is None or not title.lower().startswith("proposed work:"):
        return False
    body = issue.get("body")
    if not isinstance(body, str):
        return False
    headings = _normalized_markdown_headings(body)
    return all(
        _has_markdown_heading(headings, alternatives)
        for alternatives in PROPOSED_WORK_REQUIRED_HEADINGS
    ) and all(
        _has_markdown_heading(headings, alternatives)
        for alternatives in PROPOSED_WORK_HEADING_ALTERNATES
    )


def _handle_proposed_work_issue_label(
    database_url: str,
    payload: dict[str, Any],
    event_type: str,
    delivery_id: str,
    payload_hash: str,
    github_issue_token: str,
    opener: Callable[..., Any],
) -> dict[str, Any] | None:
    if event_type != "issues" or payload.get("action") not in PROPOSED_WORK_ACTIONS:
        return None
    issue = _payload_object(payload, "issue")
    if not issue or "pull_request" in issue or not _looks_like_proposed_work_issue(issue):
        return None
    if _issue_has_label(issue, PROPOSED_WORK_LABEL):
        return _record_status(
            database_url,
            delivery_id,
            event_type,
            payload_hash,
            "proposed_work_label_present",
        )
    repo_name = _payload_object(payload, "repository").get("full_name")
    repo = _clean_webhook_string(repo_name)
    issue_number = _github_issue_number(issue.get("number"))
    if isinstance(repo_name, str) and contains_control_character(repo_name):
        return _record_status(
            database_url,
            delivery_id,
            event_type,
            payload_hash,
            "malformed_repository",
        )
    if not repo or issue_number is None:
        return _record_status(database_url, delivery_id, event_type, payload_hash, "missing_issue")
    clean_token = github_issue_token.strip()
    if not clean_token:
        _record_event(
            database_url,
            delivery_id,
            event_type,
            payload_hash,
            "proposed_work_label_skipped",
        )
        return {
            "status": "proposed_work_label_skipped",
            "reason": "github issue token not configured",
        }
    try:
        _post_github_json(
            opener=opener,
            url=f"{_github_issue_api_base(repo, issue_number)}/labels",
            github_token=clean_token,
            payload={"labels": [PROPOSED_WORK_LABEL]},
        )
    except HTTPError as exc:
        return {
            "status": "proposed_work_label_failed",
            "reason": f"github label update failed: HTTP {exc.code}",
        }
    except (OSError, ValueError) as exc:
        return {
            "status": "proposed_work_label_failed",
            "reason": f"github label update failed: {type(exc).__name__}",
        }
    _record_event(
        database_url,
        delivery_id,
        event_type,
        payload_hash,
        "proposed_work_labeled",
    )
    return {"status": "proposed_work_labeled", "label": PROPOSED_WORK_LABEL}


def _bounty_accepts_awards(bounty: Bounty) -> bool:
    return bounty.status == "open" and bounty.awards_paid < bounty.max_awards


def _handle_accepted_issue_label(
    database_url: str,
    payload: dict[str, Any],
    event_type: str,
    delivery_id: str,
    payload_hash: str,
    accepted_labelers: tuple[str, ...] = (),
) -> dict[str, Any]:
    issue = _payload_object(payload, "issue")
    pull_request = _payload_object(payload, "pull_request")
    labeled_item = pull_request or issue
    repo_name = _payload_object(payload, "repository").get("full_name")
    repo = _clean_webhook_string(repo_name)
    issue_number = issue.get("number")
    label_name = _payload_object(payload, "label").get("name")
    label = label_name if isinstance(label_name, str) else ""
    if payload.get("action") != "labeled" or label.lower() != ACCEPTED_LABEL:
        _record_event(database_url, delivery_id, event_type, payload_hash, "ignored")
        return {"status": "ignored"}
    if isinstance(repo_name, str) and contains_control_character(repo_name):
        return _record_status(
            database_url,
            delivery_id,
            event_type,
            payload_hash,
            "malformed_repository",
        )
    if not repo or not labeled_item:
        return _record_status(database_url, delivery_id, event_type, payload_hash, "missing_issue")
    if event_type == "issues" and "pull_request" in issue:
        return _record_status(
            database_url,
            delivery_id,
            event_type,
            payload_hash,
            "ignored_pull_request_issue",
        )

    user = labeled_item.get("user") or {}
    submitter = _clean_webhook_string(user.get("login") if isinstance(user, dict) else None)
    if submitter is None:
        return _record_status(
            database_url, delivery_id, event_type, payload_hash, "missing_submitter"
        )
    sender = payload.get("sender") or {}
    sender_login = _clean_webhook_string(sender.get("login") if isinstance(sender, dict) else None)
    if sender_login is None:
        return _record_status(database_url, delivery_id, event_type, payload_hash, "missing_sender")
    accepted_by = sender_login.lower()
    if accepted_labelers and accepted_by not in accepted_labelers:
        return _record_status(
            database_url, delivery_id, event_type, payload_hash, "unauthorized_labeler"
        )
    if event_type == "issues" and accepted_labelers and submitter.lower() in accepted_labelers:
        return _record_status(
            database_url, delivery_id, event_type, payload_hash, "manual_payout_required"
        )

    bounty_issue_numbers: list[int] = []
    submission_url_value = labeled_item.get("html_url", "")
    if submission_url_value is None:
        submission_url = ""
    elif isinstance(submission_url_value, str):
        submission_url = submission_url_value
    else:
        return _record_status(
            database_url, delivery_id, event_type, payload_hash, "malformed_submission_url"
        )
    if event_type == "pull_request":
        body_value = pull_request.get("body")
        if body_value is None:
            pull_request_body = ""
        elif isinstance(body_value, str):
            pull_request_body = body_value
        else:
            return _record_status(
                database_url, delivery_id, event_type, payload_hash, "malformed_pull_request_body"
            )
        bounty_issue_numbers = _linked_issue_numbers(pull_request_body, repo)
    else:
        issue_number = _github_issue_number(issue_number)
        if issue_number is not None:
            bounty_issue_numbers = [issue_number]
    if not bounty_issue_numbers:
        return _record_status(database_url, delivery_id, event_type, payload_hash, "missing_issue")

    with session_scope(database_url) as session:
        bounty = None
        bounty_issue_number = None
        first_found_bounty = None
        first_found_issue_number = None
        for candidate in bounty_issue_numbers:
            candidate_bounty = find_bounty_by_issue(session, repo, candidate)
            if candidate_bounty is None:
                continue
            if first_found_bounty is None:
                first_found_bounty = candidate_bounty
                first_found_issue_number = candidate
            if _bounty_accepts_awards(candidate_bounty):
                bounty = candidate_bounty
                bounty_issue_number = candidate
                break
        if bounty is None:
            bounty = first_found_bounty
            bounty_issue_number = first_found_issue_number
        if bounty is None:
            session.add(
                WebhookEvent(
                    delivery_id=delivery_id,
                    event_type=event_type,
                    payload_hash=payload_hash,
                    processed_status="bounty_not_found",
                )
            )
            return {"status": "bounty_not_found"}
        try:
            to_account = resolve_payout_account(session, f"github:{submitter}")
            proof = pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account=to_account,
                submission_url=submission_url or bounty.issue_url,
                accepted_by=accepted_by,
                verifier_result={
                    "event": event_type,
                    "label": ACCEPTED_LABEL,
                    "delivery_id": delivery_id,
                    "bounty_issue_number": bounty_issue_number,
                },
            )
        except LedgerError as exc:
            session.add(
                WebhookEvent(
                    delivery_id=delivery_id,
                    event_type=event_type,
                    payload_hash=payload_hash,
                    processed_status=str(exc).replace(" ", "_"),
                )
            )
            return {"status": str(exc).replace(" ", "_")}
        session.add(
            WebhookEvent(
                delivery_id=delivery_id,
                event_type=event_type,
                payload_hash=payload_hash,
                processed_status="paid",
            )
        )
        return {"status": "paid", "proof_hash": proof.hash}


def handle_github_webhook(
    database_url: str,
    headers: dict[str, str],
    body: bytes,
    webhook_secret: str,
    accepted_labelers: tuple[str, ...] = (),
    github_issue_token: str = "",
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    signature = headers.get("X-Hub-Signature-256")
    if not verify_github_signature(body, signature, webhook_secret):
        return {"status": "unauthorized"}

    delivery_id = headers.get("X-GitHub-Delivery", "")
    event_type = headers.get("X-GitHub-Event", "")
    if not delivery_id:
        return {"status": "missing_delivery"}
    hashed = _payload_hash(body)
    with session_scope(database_url) as session:
        existing = session.get(WebhookEvent, delivery_id)
        if existing is not None:
            if existing.payload_hash != hashed:
                return {"status": "delivery_payload_mismatch"}
            return {"status": "duplicate", "processed_status": existing.processed_status}

    try:
        payload = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        _record_event(database_url, delivery_id, event_type, hashed, "invalid_payload")
        return {"status": "invalid_payload"}
    if not isinstance(payload, dict):
        _record_event(database_url, delivery_id, event_type, hashed, "invalid_payload")
        return {"status": "invalid_payload"}
    proposed_work_result = _handle_proposed_work_issue_label(
        database_url,
        payload,
        event_type,
        delivery_id,
        hashed,
        github_issue_token,
        opener,
    )
    if proposed_work_result is not None:
        return proposed_work_result
    if event_type in {"issues", "pull_request"}:
        return _handle_accepted_issue_label(
            database_url, payload, event_type, delivery_id, hashed, accepted_labelers
        )

    _record_event(database_url, delivery_id, event_type, hashed, "ignored")
    return {"status": "ignored"}
