from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from app.db import session_scope
from app.ledger.service import (
    LedgerError,
    find_bounty_by_issue,
    pay_bounty,
    resolve_payout_account,
)
from app.models import WebhookEvent

ACCEPTED_LABEL = "mrwk:accepted"
LINKED_ISSUE_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|references?|bounty)\s+"
    r"(?:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(?P<repo_number>\d+)|#(?P<number>\d+))",
    re.IGNORECASE,
)
GITHUB_ISSUE_URL_RE = re.compile(
    r"https://github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(?P<number>\d+)",
    re.IGNORECASE,
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
        if number is not None and int(number) not in numbers:
            numbers.append(int(number))
    for match in GITHUB_ISSUE_URL_RE.finditer(body):
        if match.group("repo").lower() != current_repo.lower():
            continue
        number = int(match.group("number"))
        if number not in numbers:
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


def _handle_accepted_issue_label(
    database_url: str,
    payload: dict[str, Any],
    event_type: str,
    delivery_id: str,
    payload_hash: str,
    accepted_labelers: tuple[str, ...] = (),
) -> dict[str, Any]:
    issue = payload.get("issue") or {}
    pull_request = payload.get("pull_request") or {}
    labeled_item = pull_request or issue
    repo = (payload.get("repository") or {}).get("full_name")
    issue_number = issue.get("number")
    label = (payload.get("label") or {}).get("name", "")
    if payload.get("action") != "labeled" or label.lower() != ACCEPTED_LABEL:
        _record_event(database_url, delivery_id, event_type, payload_hash, "ignored")
        return {"status": "ignored"}
    if not repo or not isinstance(labeled_item, dict):
        return _record_status(database_url, delivery_id, event_type, payload_hash, "missing_issue")

    submitter = ((labeled_item.get("user") or {}).get("login") or "unknown").strip()
    accepted_by = ((payload.get("sender") or {}).get("login") or "maintainer").strip().lower()
    if accepted_labelers and accepted_by not in accepted_labelers:
        return _record_status(
            database_url, delivery_id, event_type, payload_hash, "unauthorized_labeler"
        )
    if event_type == "issues" and accepted_labelers and submitter.lower() in accepted_labelers:
        return _record_status(
            database_url, delivery_id, event_type, payload_hash, "manual_payout_required"
        )

    bounty_issue_numbers: list[int] = []
    submission_url = labeled_item.get("html_url", "")
    if event_type == "pull_request":
        bounty_issue_numbers = _linked_issue_numbers(str(pull_request.get("body") or ""), repo)
    elif isinstance(issue_number, int):
        bounty_issue_numbers = [issue_number]
    if not bounty_issue_numbers:
        return _record_status(database_url, delivery_id, event_type, payload_hash, "missing_issue")

    with session_scope(database_url) as session:
        bounty = None
        bounty_issue_number = None
        for candidate in bounty_issue_numbers:
            bounty = find_bounty_by_issue(session, repo, candidate)
            if bounty is not None:
                bounty_issue_number = candidate
                break
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
    if event_type in {"issues", "pull_request", "label", "check_suite", "push"}:
        return _handle_accepted_issue_label(
            database_url, payload, event_type, delivery_id, hashed, accepted_labelers
        )

    _record_event(database_url, delivery_id, event_type, hashed, "ignored")
    return {"status": "ignored"}
