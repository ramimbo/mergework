from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.ledger.service import (
    TREASURY_ACCOUNT,
    format_mrwk,
    get_balance,
    register_wallet,
    submit_wallet_transfer,
)
from app.models import Bounty, BountyAttempt, LedgerEntry, Proof, Wallet
from app.serializers import (
    bounty_awards_to_dict,
    bounty_to_dict,
    ledger_to_dict,
    wallet_to_dict,
    wallet_transfer_to_dict,
)
from app.wallets import WalletError, normalize_wallet_address

GITHUB_LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")
HEX_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SQLITE_INTEGER_MAX = 2**63 - 1


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _attempt_effective_status(attempt: BountyAttempt, now: datetime) -> str:
    if attempt.status == "active" and _as_utc(attempt.expires_at) <= now:
        return "expired"
    return attempt.status


def _active_attempt_conditions(bounty_id: int, now: datetime) -> tuple[Any, ...]:
    return (
        BountyAttempt.bounty_id == bounty_id,
        BountyAttempt.status == "active",
        BountyAttempt.expires_at > now,
    )


def _bounty_attempt_to_dict(attempt: BountyAttempt, now: datetime | None = None) -> dict[str, Any]:
    now = now or _utc_now()
    return {
        "id": attempt.id,
        "bounty_id": attempt.bounty_id,
        "submitter_account": attempt.submitter_account,
        "source_url": attempt.source_url,
        "status": _attempt_effective_status(attempt, now),
        "expires_at": _as_utc(attempt.expires_at).isoformat(),
        "created_at": _as_utc(attempt.created_at).isoformat(),
        "updated_at": _as_utc(attempt.updated_at).isoformat(),
    }


def _bounty_attempt_warnings(session: Session, bounty: Bounty, now: datetime) -> list[str]:
    warnings: list[str] = []
    awards_remaining = max(0, bounty.max_awards - bounty.awards_paid)
    if bounty.status != "open":
        warnings.append(f"bounty is {bounty.status}")
        awards_remaining = 0
    if awards_remaining <= 0:
        warnings.append("bounty has no award slots remaining")
    active_count = session.scalar(
        select(func.count())
        .select_from(BountyAttempt)
        .where(*_active_attempt_conditions(bounty.id, now))
    )
    if active_count and active_count > 1:
        warnings.append(f"bounty has {active_count} active attempts")
    return warnings


def _normalized_wallet_address(address: str) -> str:
    try:
        return normalize_wallet_address(address)
    except WalletError as exc:
        raise ValueError(str(exc)) from exc


def _normalized_account(account: str) -> str:
    if not account or not account.strip():
        raise ValueError("account must not be empty")
    if re.search(r"[\x00-\x1f\x7f]", account):
        raise ValueError("account must not contain control characters")
    clean = account.strip()
    lower = clean.lower()
    if lower == TREASURY_ACCOUNT:
        return TREASURY_ACCOUNT
    if lower.startswith("treasury:"):
        raise ValueError("treasury account must be treasury:mrwk")
    if lower.startswith("reserve:"):
        reserve_prefix = "reserve:bounty:"
        if not lower.startswith(reserve_prefix):
            raise ValueError("reserve account must use reserve:bounty:<id>")
        bounty_id = lower.removeprefix(reserve_prefix)
        try:
            normalized_bounty_id = int(bounty_id) if bounty_id.isdigit() else 0
        except ValueError as exc:
            raise ValueError("reserve bounty id is too large") from exc
        if normalized_bounty_id <= 0:
            raise ValueError("reserve bounty id must be positive")
        if normalized_bounty_id > SQLITE_INTEGER_MAX:
            raise ValueError("reserve bounty id is too large")
        return f"{reserve_prefix}{normalized_bounty_id}"
    if lower.startswith("mrwk1"):
        return _normalized_wallet_address(clean)
    if lower.startswith("github:"):
        login = clean.split(":", 1)[1].lower()
        if not GITHUB_LOGIN_RE.fullmatch(login):
            raise ValueError("github login must be valid")
        return f"github:{login}"
    return clean


def _proof_hash_from_arg(proof_hash: str) -> str:
    if proof_hash != proof_hash.strip():
        raise ValueError("proof hash must be 64 hex characters")
    clean = proof_hash.lower()
    if not HEX_HASH_RE.fullmatch(clean):
        raise ValueError("proof hash must be 64 hex characters")
    return clean


def call_mcp_tool(database_url: str, name: str, args: dict[str, Any]) -> str | dict[str, Any]:
    def int_arg(field: str) -> int:
        value = args[field]
        if isinstance(value, bool):
            raise ValueError(f"{field} must be an integer")
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str):
            clean = value.strip()
            if clean and clean.lstrip("+-").isdigit():
                try:
                    parsed = int(clean)
                except ValueError as exc:
                    raise ValueError(f"{field} must be an integer") from exc
            else:
                raise ValueError(f"{field} must be an integer")
        else:
            raise ValueError(f"{field} must be an integer")
        if parsed < -SQLITE_INTEGER_MAX - 1 or parsed > SQLITE_INTEGER_MAX:
            raise ValueError(f"{field} is too large")
        return parsed

    def positive_int_arg(field: str) -> int:
        value = int_arg(field)
        if value <= 0:
            raise ValueError(f"{field} must be positive")
        return value

    def str_arg(field: str, *, allow_empty: bool = False) -> str:
        value = args[field]
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        if not allow_empty and value == "":
            raise ValueError(f"{field} must not be empty")
        return value

    def optional_str_arg(field: str, default: str = "") -> str:
        value = args.get(field, default)
        if value is None:
            return default
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        return value

    def optional_clean_str_arg(field: str) -> str | None:
        value = args.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        clean = value.strip()
        return clean or None

    def output_format_arg() -> str:
        value = args.get("format", "text")
        if value is None:
            return "text"
        if not isinstance(value, str):
            raise ValueError("format must be a string")
        normalized = value.strip().lower()
        if normalized not in {"text", "json"}:
            raise ValueError("format must be text or json")
        return normalized

    def mcp_issue_number_search_value(query_text: str) -> int | None:
        if not query_text.isdigit():
            return None
        try:
            issue_number = int(query_text)
        except ValueError:
            return None
        return issue_number if issue_number <= SQLITE_INTEGER_MAX else None

    def list_limit_arg(default: int = 25) -> int:
        if "limit" not in args or args.get("limit") is None:
            return default
        value = positive_int_arg("limit")
        if value > 100:
            raise ValueError("limit must be at most 100")
        return value

    def work_proof_guidance(bounty: Bounty) -> str:
        bounty_data = bounty_to_dict(bounty)
        availability = (
            "open for submissions"
            if bounty_data["status"] == "open" and bounty_data["awards_remaining"] > 0
            else "not currently open for new submissions"
        )
        return "\n".join(
            [
                f"Bounty #{bounty_data['issue_number']}: {bounty_data['title']}",
                f"Internal bounty id: {bounty_data['id']}",
                f"Repository: {bounty_data['repo']}",
                f"Issue: {bounty_data['issue_url']}",
                (
                    f"Status: {bounty_data['status']} ({availability}); "
                    f"awards remaining: {bounty_data['awards_remaining']} "
                    f"of {bounty_data['max_awards']}"
                ),
                f"Reward: {bounty_data['reward_mrwk']} MRWK per accepted award",
                f"Acceptance: {bounty_data['acceptance']}",
                (
                    "Submit: open a focused PR or issue that links this bounty, include "
                    "specific test or behavior evidence, then comment /claim with the PR "
                    "or evidence URL and verification summary."
                ),
                (
                    "Do not include private keys, seed material, secrets, deployment "
                    "credentials, private vulnerability details, or price claims."
                ),
            ]
        )

    def work_proof_guidance_json(bounty: Bounty) -> dict[str, Any]:
        bounty_data = bounty_to_dict(bounty)
        can_submit = bounty_data["status"] == "open" and bounty_data["awards_remaining"] > 0
        availability_warnings = []
        if bounty_data["status"] != "open":
            availability_warnings.append(f"bounty is {bounty_data['status']}")
        if bounty_data["awards_remaining"] <= 0:
            availability_warnings.append("bounty has no award slots remaining")
        return {
            "bounty_id": bounty_data["id"],
            "issue_number": bounty_data["issue_number"],
            "status": bounty_data["status"],
            "availability": "open_for_submissions" if can_submit else "not_currently_open",
            "can_submit": can_submit,
            "availability_warnings": availability_warnings,
            "awards_remaining": bounty_data["awards_remaining"],
            "max_awards": bounty_data["max_awards"],
            "awards_paid": bounty_data["awards_paid"],
            "reward_mrwk": bounty_data["reward_mrwk"],
            "available_mrwk": bounty_data["available_mrwk"],
            "repository": bounty_data["repo"],
            "issue_url": bounty_data["issue_url"],
            "title": bounty_data["title"],
            "acceptance": bounty_data["acceptance"],
            "submission_format": (
                "Open a focused PR or issue that links this bounty, include specific "
                "test or behavior evidence, then comment /claim with the PR or "
                "evidence URL and verification summary."
            ),
            "safety_rules": [
                "Do not include private keys, seed material, secrets, deployment "
                "credentials, private vulnerability details, or price claims."
            ],
        }

    def generic_work_proof_guidance_json() -> dict[str, Any]:
        return {
            "bounty_id": None,
            "issue_number": None,
            "status": "generic_guidance",
            "availability": "unknown_without_bounty",
            "can_submit": None,
            "availability_warnings": [],
            "awards_remaining": None,
            "reward_mrwk": None,
            "repository": None,
            "issue_url": None,
            "acceptance": None,
            "submission_format": (
                "Open a focused PR or issue, reference the MRWK bounty, include test "
                "evidence, and wait for a maintainer to apply mrwk:accepted."
            ),
            "safety_rules": [
                "Do not include private keys, seed material, secrets, deployment "
                "credentials, private vulnerability details, or price claims."
            ],
        }

    def optional_bool_arg(field: str, default: bool = False) -> bool:
        value = args.get(field, default)
        if value is None:
            return default
        if not isinstance(value, bool):
            raise ValueError(f"{field} must be a boolean")
        return value

    with session_scope(database_url) as session:
        if name == "list_bounties":
            status = optional_clean_str_arg("status") or "open"
            normalized_status = status.lower()
            if normalized_status not in {"open", "paid", "closed"}:
                raise ValueError("status must be one of: open, paid, closed")
            query = select(Bounty).where(Bounty.status == normalized_status)
            query_text = optional_clean_str_arg("q")
            if query_text:
                escaped_query = (
                    query_text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                like_query = f"%{escaped_query}%"
                issue_number = mcp_issue_number_search_value(query_text)
                text_filter = or_(
                    func.lower(Bounty.repo).like(like_query, escape="\\"),
                    func.lower(Bounty.title).like(like_query, escape="\\"),
                    func.lower(Bounty.acceptance).like(like_query, escape="\\"),
                )
                if issue_number is not None:
                    text_filter = or_(text_filter, Bounty.issue_number == issue_number)
                query = query.where(text_filter)
            bounties = session.scalars(
                query.order_by(Bounty.id.desc()).limit(list_limit_arg())
            ).all()
            return json.dumps([bounty_to_dict(bounty) for bounty in bounties])
        if name == "get_bounty":
            bounty = session.get(Bounty, positive_int_arg("id"))
            if bounty is None:
                return "bounty not found"
            bounty_data = bounty_to_dict(bounty)
            if optional_bool_arg("include_awards"):
                bounty_data["awards"] = bounty_awards_to_dict(session, bounty.id)
            return json.dumps(bounty_data)
        if name == "list_bounty_attempts":
            bounty_id = positive_int_arg("bounty_id")
            bounty = session.get(Bounty, bounty_id)
            if bounty is None:
                return "bounty not found"
            now = _utc_now()
            attempt_query = select(BountyAttempt).where(BountyAttempt.bounty_id == bounty_id)
            if not optional_bool_arg("include_expired"):
                attempt_query = attempt_query.where(*_active_attempt_conditions(bounty_id, now))
            attempts = session.scalars(
                attempt_query.order_by(
                    BountyAttempt.created_at.desc(), BountyAttempt.id.desc()
                ).limit(list_limit_arg())
            ).all()
            return {
                "bounty_id": bounty_id,
                "issue_number": bounty.issue_number,
                "status": bounty.status,
                "warnings": _bounty_attempt_warnings(session, bounty, now),
                "attempts": [_bounty_attempt_to_dict(attempt, now) for attempt in attempts],
            }
        if name == "get_balance":
            account = _normalized_account(str_arg("account"))
            return f"{account}: {format_mrwk(get_balance(session, account))} MRWK"
        if name == "register_wallet":
            wallet = register_wallet(
                session,
                public_key_hex=str_arg("public_key_hex"),
                label=optional_str_arg("label") if args.get("label") is not None else None,
            )
            return json.dumps(wallet_to_dict(session, wallet))
        if name == "get_wallet":
            wallet_row = session.get(Wallet, _normalized_wallet_address(str_arg("address")))
            if wallet_row is None:
                return "wallet not found"
            return json.dumps(wallet_to_dict(session, wallet_row))
        if name == "submit_wallet_transfer":
            transfer = submit_wallet_transfer(
                session,
                from_address=str_arg("from_address"),
                to_address=str_arg("to_address"),
                amount_mrwk=str_arg("amount_mrwk"),
                nonce=int_arg("nonce"),
                memo=optional_str_arg("memo"),
                signature_hex=str_arg("signature_hex"),
            )
            return json.dumps(wallet_transfer_to_dict(transfer))
        if name == "get_ledger_entry":
            entry = session.get(LedgerEntry, positive_int_arg("sequence"))
            if entry is None:
                return "ledger entry not found"
            proof = session.scalar(
                select(Proof).where(Proof.ledger_sequence == entry.sequence).limit(1)
            )
            return json.dumps(ledger_to_dict(entry, proof.hash if proof else None))
        if name == "get_proof":
            proof = session.get(Proof, _proof_hash_from_arg(str_arg("hash")))
            if proof is None:
                return "proof not found"
            public_payload = json.loads(proof.public_json)
            if not isinstance(public_payload, dict):
                raise ValueError("invalid proof payload")
            return json.dumps(
                {
                    "hash": proof.hash,
                    "kind": proof.kind,
                    "ledger_sequence": proof.ledger_sequence,
                    "bounty_id": proof.bounty_id,
                    "submission_id": proof.submission_id,
                    "created_at": proof.created_at.isoformat(),
                    "proof": public_payload,
                }
            )
        if name == "submit_work_proof":
            output_format = output_format_arg()
            has_bounty_id = "bounty_id" in args and args.get("bounty_id") is not None
            has_issue_number = "issue_number" in args and args.get("issue_number") is not None
            if has_bounty_id and has_issue_number:
                raise ValueError("use bounty_id or issue_number, not both")
            if has_bounty_id:
                bounty = session.get(Bounty, positive_int_arg("bounty_id"))
                if bounty is None:
                    return "bounty not found"
                return (
                    work_proof_guidance_json(bounty)
                    if output_format == "json"
                    else work_proof_guidance(bounty)
                )
            if has_issue_number:
                bounties = session.scalars(
                    select(Bounty)
                    .where(Bounty.issue_number == positive_int_arg("issue_number"))
                    .order_by(Bounty.id.desc())
                    .limit(2)
                ).all()
                if not bounties:
                    return "bounty not found"
                if len(bounties) > 1:
                    raise ValueError("issue_number matches multiple bounties")
                return (
                    work_proof_guidance_json(bounties[0])
                    if output_format == "json"
                    else work_proof_guidance(bounties[0])
                )
            if output_format == "json":
                return generic_work_proof_guidance_json()
            return (
                "Open a focused PR or issue, reference the MRWK bounty, include test evidence, "
                "and wait for a maintainer to apply mrwk:accepted."
            )
    raise ValueError("unknown tool")
