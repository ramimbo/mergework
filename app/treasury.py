from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ledger.service import (
    MICRO_UNITS,
    TREASURY_ACCOUNT,
    LedgerError,
    canonical_json,
    close_bounty,
    create_bounty,
    format_mrwk,
    get_balance,
    linked_wallet_for_github,
    parse_mrwk_amount,
    pay_bounty,
    reserve_account_for_bounty,
    resolve_payout_account,
    validate_public_url,
)
from app.models import (
    Bounty,
    LedgerEntry,
    Proof,
    Submission,
    TreasuryChallenge,
    TreasuryProposal,
    utc_now,
)
from app.path_params import SQLITE_INTEGER_MAX
from app.serializers import bounty_to_dict

TREASURY_PROPOSAL_DELAY = timedelta(hours=24)
TREASURY_EPOCH_WINDOW = timedelta(hours=24)
TREASURY_EPOCH_RESERVE_CAP_MICRO = 10_000 * MICRO_UNITS
TREASURY_ACTIONS = {"create_bounty", "pay_bounty", "close_bounty"}
SUBJECTIVE_CHALLENGE = "subjective_note"
MACHINE_CHALLENGES = {
    "duplicate_bounty",
    "bounty_not_open",
    "submission_already_paid",
    "insufficient_reserve",
    "epoch_cap_exceeded",
}
CHALLENGE_TYPES = MACHINE_CHALLENGES | {SUBJECTIVE_CHALLENGE}


def _db_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _db_now() -> datetime:
    return _db_utc(utc_now())


def _required_payload_value(payload: dict[str, Any], field: str) -> Any:
    value = payload.get(field)
    if value is None:
        raise LedgerError(f"{field} is required")
    return value


def _contains_control_character(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 or 0x80 <= ord(char) <= 0x9F for char in value)


def _clean_string(value: Any, field: str, max_length: int = 500) -> str:
    if not isinstance(value, str):
        raise LedgerError(f"{field} must be a string")
    if _contains_control_character(value):
        raise LedgerError(f"{field} must not contain control characters")
    clean = value.strip()
    if not clean:
        raise LedgerError(f"{field} is required")
    if len(clean) > max_length:
        raise LedgerError(f"{field} is too long")
    return clean


def _optional_string(value: Any, field: str, max_length: int = 500) -> str | None:
    if value is None:
        return None
    return _clean_string(value, field, max_length)


def _payload_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise LedgerError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if _contains_control_character(value):
            raise LedgerError(f"{field} must not contain control characters")
        clean = value.strip()
        if clean and clean.lstrip("+-").isdigit():
            try:
                return int(clean)
            except ValueError as exc:
                raise LedgerError(f"{field} must be an integer") from exc
    raise LedgerError(f"{field} must be an integer")


def _canonical_payload(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    if action == "create_bounty":
        issue_number = _payload_int(
            _required_payload_value(payload, "issue_number"), "issue_number"
        )
        max_awards = _payload_int(payload.get("max_awards", 1), "max_awards")
        if issue_number <= 0:
            raise LedgerError("issue_number must be positive")
        if max_awards <= 0:
            raise LedgerError("max_awards must be positive")
        if issue_number > SQLITE_INTEGER_MAX:
            raise LedgerError("issue_number is too large")
        if max_awards > 1_000:
            raise LedgerError("max_awards is too large")
        raw_reward_mrwk = str(_required_payload_value(payload, "reward_mrwk"))
        parse_mrwk_amount(raw_reward_mrwk)
        reward_mrwk = raw_reward_mrwk.strip()
        return {
            "repo": _clean_string(_required_payload_value(payload, "repo"), "repo", 200).lower(),
            "issue_number": issue_number,
            "issue_url": validate_public_url(
                _clean_string(_required_payload_value(payload, "issue_url"), "issue_url", 500)
            ),
            "title": _clean_string(_required_payload_value(payload, "title"), "title", 300),
            "reward_mrwk": reward_mrwk,
            "max_awards": max_awards,
            "acceptance": _clean_string(
                _required_payload_value(payload, "acceptance"), "acceptance", 5_000
            ),
        }
    if action == "pay_bounty":
        bounty_id = _payload_int(_required_payload_value(payload, "bounty_id"), "bounty_id")
        if bounty_id <= 0:
            raise LedgerError("bounty id must be positive")
        if bounty_id > SQLITE_INTEGER_MAX:
            raise LedgerError("bounty id is too large")
        clean: dict[str, Any] = {
            "bounty_id": bounty_id,
            "to_account": _clean_string(
                _required_payload_value(payload, "to_account"), "to_account", 128
            ),
            "submission_url": validate_public_url(
                _clean_string(
                    _required_payload_value(payload, "submission_url"),
                    "submission_url",
                    500,
                )
            ),
            "accepted_by": _clean_string(
                _required_payload_value(payload, "accepted_by"), "accepted_by", 80
            ),
        }
        note = _optional_string(payload.get("note"), "note", 240)
        if note:
            clean["note"] = note
        return clean
    if action == "close_bounty":
        bounty_id = _payload_int(_required_payload_value(payload, "bounty_id"), "bounty_id")
        if bounty_id <= 0:
            raise LedgerError("bounty id must be positive")
        if bounty_id > SQLITE_INTEGER_MAX:
            raise LedgerError("bounty id is too large")
        reference = _optional_string(payload.get("reference"), "reference", 500)
        clean = {
            "bounty_id": bounty_id,
            "closed_by": _clean_string(
                _required_payload_value(payload, "closed_by"), "closed_by", 80
            ),
            "reference": validate_public_url(reference) if reference else None,
        }
        return clean
    raise LedgerError("unsupported treasury action")


def _github_issue_url_matches(repo: str, issue_number: int, issue_url: str) -> bool:
    parsed = urlparse(issue_url)
    if (parsed.hostname or "").lower() != "github.com":
        return False
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 4 or parts[2] != "issues" or not parts[3].isdigit():
        return False
    return f"{parts[0]}/{parts[1]}".lower() == repo.lower() and int(parts[3]) == issue_number


def _pending_action_payloads(
    session: Session, action: str
) -> list[tuple[TreasuryProposal, dict[str, Any]]]:
    proposals = session.scalars(
        select(TreasuryProposal)
        .where(TreasuryProposal.status == "pending", TreasuryProposal.action == action)
        .order_by(TreasuryProposal.id.asc())
    ).all()
    return [(proposal, proposal_payload(proposal)) for proposal in proposals]


def _pending_create_bounty_reserved_microunits(
    session: Session, *, through_proposal_id: int | None = None
) -> int:
    total = 0
    for proposal, payload in _pending_action_payloads(session, "create_bounty"):
        if through_proposal_id is not None and int(proposal.id) > through_proposal_id:
            continue
        total += parse_mrwk_amount(str(payload["reward_mrwk"])) * int(payload["max_awards"])
    return total


def _has_pending_create_bounty_proposal(
    session: Session,
    *,
    repo: str,
    issue_number: int,
    exclude_proposal_id: int | None = None,
) -> bool:
    for proposal, payload in _pending_action_payloads(session, "create_bounty"):
        if exclude_proposal_id is not None and proposal.id == exclude_proposal_id:
            continue
        if (
            str(payload["repo"]).lower() == repo.lower()
            and int(payload["issue_number"]) == issue_number
        ):
            return True
    return False


def _pending_pay_bounty_payloads(
    session: Session, bounty_id: int, *, through_proposal_id: int | None = None
) -> list[tuple[TreasuryProposal, dict[str, Any]]]:
    pending: list[tuple[TreasuryProposal, dict[str, Any]]] = []
    for proposal, payload in _pending_action_payloads(session, "pay_bounty"):
        if through_proposal_id is not None and int(proposal.id) > through_proposal_id:
            continue
        if int(payload["bounty_id"]) == bounty_id:
            pending.append((proposal, payload))
    return pending


def _has_pending_pay_bounty_proposal(
    session: Session,
    *,
    bounty_id: int,
    submission_url: str | None = None,
    exclude_proposal_id: int | None = None,
) -> bool:
    for proposal, payload in _pending_pay_bounty_payloads(session, bounty_id):
        if exclude_proposal_id is not None and proposal.id == exclude_proposal_id:
            continue
        if submission_url is None or str(payload["submission_url"]) == submission_url:
            return True
    return False


def _has_pending_close_bounty_proposal(
    session: Session, *, bounty_id: int, exclude_proposal_id: int | None = None
) -> bool:
    for proposal, payload in _pending_action_payloads(session, "close_bounty"):
        if exclude_proposal_id is not None and proposal.id == exclude_proposal_id:
            continue
        if int(payload["bounty_id"]) == bounty_id:
            return True
    return False


def _validate_create_bounty_proposal(session: Session, payload: dict[str, Any]) -> None:
    repo = str(payload["repo"])
    issue_number = int(payload["issue_number"])
    issue_url = str(payload["issue_url"])
    if not _github_issue_url_matches(repo, issue_number, issue_url):
        raise LedgerError("issue_url must match repo and issue_number")
    if _has_pending_create_bounty_proposal(session, repo=repo, issue_number=issue_number):
        raise LedgerError("create_bounty proposal already pending")
    reserved = parse_mrwk_amount(str(payload["reward_mrwk"])) * int(payload["max_awards"])
    if (
        _epoch_reserved_microunits(session, _db_now())
        + _pending_create_bounty_reserved_microunits(session)
        + reserved
        > TREASURY_EPOCH_RESERVE_CAP_MICRO
    ):
        raise LedgerError("treasury epoch reserve cap exceeded")


def _existing_submission_for_payload(
    session: Session, *, bounty_id: int, submission_url: str
) -> Submission | None:
    return session.scalar(
        select(Submission)
        .where(Submission.bounty_id == bounty_id, Submission.url == submission_url)
        .limit(1)
    )


def _validate_pay_bounty_proposal(session: Session, payload: dict[str, Any]) -> None:
    bounty_id = int(payload["bounty_id"])
    bounty = session.get(Bounty, bounty_id)
    if bounty is None:
        raise LedgerError("bounty not found")
    if bounty.status != "open":
        raise LedgerError("bounty is not open")
    if _has_pending_close_bounty_proposal(session, bounty_id=bounty_id):
        raise LedgerError("bounty has pending close proposal")
    submission_url = str(payload["submission_url"])
    if (
        _existing_submission_for_payload(
            session, bounty_id=bounty_id, submission_url=submission_url
        )
        is not None
    ):
        raise LedgerError("submission already paid")
    pending_payouts = _pending_pay_bounty_payloads(session, bounty_id)
    if any(
        str(pending_payload["submission_url"]) == submission_url
        for _, pending_payload in pending_payouts
    ):
        raise LedgerError("pay_bounty proposal already pending for submission")
    requested_awards = len(pending_payouts) + 1
    if bounty.awards_paid + requested_awards > bounty.max_awards:
        raise LedgerError("pending payout proposals exceed bounty remaining awards")
    if get_balance(session, reserve_account_for_bounty(bounty.id)) < (
        bounty.reward_microunits * requested_awards
    ):
        raise LedgerError("pending payout proposals exceed bounty reserve")


def _validate_close_bounty_proposal(session: Session, payload: dict[str, Any]) -> None:
    bounty_id = int(payload["bounty_id"])
    bounty = session.get(Bounty, bounty_id)
    if bounty is None:
        raise LedgerError("bounty not found")
    if bounty.status != "open":
        raise LedgerError("bounty is not open")
    if _has_pending_close_bounty_proposal(session, bounty_id=bounty_id):
        raise LedgerError("close_bounty proposal already pending")
    if _has_pending_pay_bounty_proposal(session, bounty_id=bounty_id):
        raise LedgerError("bounty has pending payout proposals")


def _proposal_hash(action: str, payload: dict[str, Any]) -> str:
    body = canonical_json({"action": action, "payload": payload})
    return hashlib.sha256(body.encode()).hexdigest()


def proposal_payload(proposal: TreasuryProposal) -> dict[str, Any]:
    data = json.loads(proposal.payload_json)
    if not isinstance(data, dict):
        raise LedgerError("invalid proposal payload")
    return data


def proposal_result(proposal: TreasuryProposal) -> dict[str, Any]:
    data = json.loads(proposal.result_json)
    return data if isinstance(data, dict) else {}


def challenge_to_dict(challenge: TreasuryChallenge) -> dict[str, Any]:
    return {
        "id": challenge.id,
        "proposal_id": challenge.proposal_id,
        "challenger_account": challenge.challenger_account,
        "challenge_type": challenge.challenge_type,
        "status": challenge.status,
        "reason": challenge.reason,
        "created_at": challenge.created_at.isoformat(),
    }


def proposal_to_dict(proposal: TreasuryProposal) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "type": "treasury_proposal",
        "action": proposal.action,
        "status": proposal.status,
        "payload_hash": proposal.payload_hash,
        "payload": proposal_payload(proposal),
        "proposed_by": proposal.proposed_by,
        "executed_by": proposal.executed_by,
        "proposed_at": proposal.proposed_at.isoformat(),
        "executes_after": proposal.executes_after.isoformat(),
        "executed_at": proposal.executed_at.isoformat() if proposal.executed_at else None,
        "executed_ledger_sequence": proposal.executed_ledger_sequence,
        "result": proposal_result(proposal),
        "challenges": [challenge_to_dict(challenge) for challenge in proposal.challenges],
    }


def record_proposal_result_field(
    session: Session, *, proposal_id: int, field: str, value: Any
) -> TreasuryProposal:
    proposal = session.get(TreasuryProposal, proposal_id)
    if proposal is None:
        raise LedgerError("proposal not found")
    result = proposal_result(proposal)
    result[_clean_string(field, "field", 80)] = value
    proposal.result_json = canonical_json(result)
    session.flush()
    return proposal


def propose_treasury_action(
    session: Session,
    *,
    action: str,
    payload: dict[str, Any],
    proposed_by: str,
) -> TreasuryProposal:
    clean_action = _clean_string(action, "action", 40)
    if clean_action not in TREASURY_ACTIONS:
        raise LedgerError("unsupported treasury action")
    clean_payload = _canonical_payload(clean_action, payload)
    if clean_action == "pay_bounty":
        clean_payload["to_account"] = resolve_payout_account(
            session, str(clean_payload["to_account"])
        )
        _validate_pay_bounty_proposal(session, clean_payload)
    elif clean_action == "create_bounty":
        _validate_create_bounty_proposal(session, clean_payload)
    elif clean_action == "close_bounty":
        _validate_close_bounty_proposal(session, clean_payload)
    now = _db_now()
    proposal = TreasuryProposal(
        action=clean_action,
        status="pending",
        payload_json=canonical_json(clean_payload),
        payload_hash=_proposal_hash(clean_action, clean_payload),
        proposed_by=_clean_string(proposed_by, "proposed_by", 128),
        result_json="{}",
        proposed_at=now,
        executes_after=now + TREASURY_PROPOSAL_DELAY,
    )
    session.add(proposal)
    session.flush()
    return proposal


def _epoch_reserved_microunits(session: Session, now: datetime) -> int:
    since = now - TREASURY_EPOCH_WINDOW
    amount = session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_microunits), 0)).where(
            LedgerEntry.entry_type == "bounty_reserve",
            LedgerEntry.from_account == TREASURY_ACCOUNT,
            LedgerEntry.created_at >= since,
        )
    )
    return int(amount or 0)


def _recent_bounty_reserve_entries(session: Session, now: datetime) -> list[LedgerEntry]:
    since = now - TREASURY_EPOCH_WINDOW
    return list(
        session.scalars(
            select(LedgerEntry)
            .where(
                LedgerEntry.entry_type == "bounty_reserve",
                LedgerEntry.from_account == TREASURY_ACCOUNT,
                LedgerEntry.created_at >= since,
            )
            .order_by(LedgerEntry.created_at.asc(), LedgerEntry.sequence.asc())
        ).all()
    )


def _projected_capacity_events(
    *,
    now: datetime,
    executed_reserve: int,
    pending_create_reserve: int,
    recent_reserves: list[LedgerEntry],
    pending_creates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    event_rows: list[tuple[datetime, int, str, int, str]] = []
    for entry in recent_reserves:
        releases_at = _db_utc(entry.created_at) + TREASURY_EPOCH_WINDOW
        if releases_at > now:
            event_rows.append(
                (
                    releases_at,
                    0,
                    "recent_reserve_releases",
                    int(entry.amount_microunits),
                    "Executed reserve leaves the 24h cap window.",
                )
            )
    for pending in pending_creates:
        executes_after = pending["executes_after_dt"]
        if executes_after < now:
            executes_after = now
        reserve = int(pending["reserve_microunits"])
        event_rows.append(
            (
                executes_after,
                1,
                "pending_create_executes",
                reserve,
                "Pending create reserve becomes executed reserve; capacity does not increase.",
            )
        )
        event_rows.append(
            (
                executes_after + TREASURY_EPOCH_WINDOW,
                2,
                "pending_create_releases",
                reserve,
                "Executed reserve from this pending create leaves the 24h cap window.",
            )
        )

    projected_executed = executed_reserve
    projected_pending = pending_create_reserve
    events: list[dict[str, Any]] = []
    for at, _, event_type, amount, note in sorted(event_rows, key=lambda item: (item[0], item[1])):
        if event_type == "recent_reserve_releases":
            projected_executed = max(projected_executed - amount, 0)
        elif event_type == "pending_create_executes":
            projected_pending = max(projected_pending - amount, 0)
            projected_executed += amount
        elif event_type == "pending_create_releases":
            projected_executed = max(projected_executed - amount, 0)
        available = max(
            TREASURY_EPOCH_RESERVE_CAP_MICRO - projected_executed - projected_pending,
            0,
        )
        events.append(
            {
                "at": at.isoformat(),
                "event_type": event_type,
                "amount_mrwk": format_mrwk(amount),
                "available_create_reserve_mrwk": format_mrwk(available),
                "note": note,
            }
        )
    return events


def treasury_status(session: Session) -> dict[str, Any]:
    now = _db_now()
    recent_reserves = _recent_bounty_reserve_entries(session, now)
    executed_reserve = sum(entry.amount_microunits for entry in recent_reserves)
    pending_create_bounties: list[dict[str, Any]] = []
    pending_create_projection: list[dict[str, Any]] = []
    pending_create_reserve = 0
    for proposal, payload in _pending_action_payloads(session, "create_bounty"):
        reserve = parse_mrwk_amount(str(payload["reward_mrwk"])) * int(payload["max_awards"])
        executes_after = _db_utc(proposal.executes_after)
        projected_executes_after = max(executes_after, now)
        capacity_releases_at = projected_executes_after + TREASURY_EPOCH_WINDOW
        pending_create_reserve += reserve
        pending_create_bounties.append(
            {
                "proposal_id": proposal.id,
                "issue_number": int(payload["issue_number"]),
                "issue_url": str(payload["issue_url"]),
                "title": str(payload["title"]),
                "reward_mrwk": str(payload["reward_mrwk"]),
                "max_awards": int(payload["max_awards"]),
                "reserve_mrwk": format_mrwk(reserve),
                "proposed_at": _db_utc(proposal.proposed_at).isoformat(),
                "executes_after": executes_after.isoformat(),
                "capacity_releases_at": capacity_releases_at.isoformat(),
            }
        )
        pending_create_projection.append(
            {
                "executes_after_dt": projected_executes_after,
                "reserve_microunits": reserve,
            }
        )
    available = max(TREASURY_EPOCH_RESERVE_CAP_MICRO - executed_reserve - pending_create_reserve, 0)
    release_times = [
        _db_utc(entry.created_at) + TREASURY_EPOCH_WINDOW
        for entry in recent_reserves
        if _db_utc(entry.created_at) + TREASURY_EPOCH_WINDOW > now
    ]
    next_capacity_release_at = min(release_times).isoformat() if release_times else None
    projected_capacity_events = _projected_capacity_events(
        now=now,
        executed_reserve=executed_reserve,
        pending_create_reserve=pending_create_reserve,
        recent_reserves=recent_reserves,
        pending_creates=pending_create_projection,
    )
    projected_release_events = [
        event["at"]
        for event in projected_capacity_events
        if event["event_type"] in {"recent_reserve_releases", "pending_create_releases"}
    ]
    return {
        "type": "treasury_status",
        "epoch_window_hours": int(TREASURY_EPOCH_WINDOW.total_seconds() // 3600),
        "reserve_cap_mrwk": format_mrwk(TREASURY_EPOCH_RESERVE_CAP_MICRO),
        "executed_reserve_24h_mrwk": format_mrwk(executed_reserve),
        "pending_create_reserve_mrwk": format_mrwk(pending_create_reserve),
        "available_create_reserve_mrwk": format_mrwk(available),
        "next_capacity_release_at": next_capacity_release_at,
        "next_projected_capacity_release_at": (
            min(projected_release_events) if projected_release_events else None
        ),
        "pending_create_bounties": pending_create_bounties,
        "projected_capacity_events": projected_capacity_events,
        "recent_reserves": [
            {
                "ledger_sequence": entry.sequence,
                "amount_mrwk": format_mrwk(entry.amount_microunits),
                "reference": entry.reference,
                "created_at": _db_utc(entry.created_at).isoformat(),
                "expires_at": (_db_utc(entry.created_at) + TREASURY_EPOCH_WINDOW).isoformat(),
            }
            for entry in recent_reserves
        ],
    }


def _payout_response_from_proof(proof: Proof) -> dict[str, Any]:
    data = json.loads(proof.public_json)
    if not isinstance(data, dict) or data.get("kind") != "bounty_payment":
        raise LedgerError("invalid proof payload")
    return {
        "status": "paid",
        "bounty_id": proof.bounty_id,
        "to_account": data.get("to_account"),
        "submission_id": proof.submission_id,
        "submission_url": data.get("submission_url"),
        "ledger_sequence": proof.ledger_sequence,
        "ledger_url": f"/ledger/{proof.ledger_sequence}",
        "proof_hash": proof.hash,
        "proof_url": f"/proofs/{proof.hash}",
    }


def _execute_create_bounty(
    session: Session, payload: dict[str, Any], now: datetime
) -> tuple[dict[str, Any], int | None]:
    reward = parse_mrwk_amount(str(payload["reward_mrwk"]))
    reserved = reward * int(payload["max_awards"])
    if _epoch_reserved_microunits(session, now) + reserved > TREASURY_EPOCH_RESERVE_CAP_MICRO:
        raise LedgerError("treasury epoch reserve cap exceeded")
    bounty = create_bounty(
        session,
        repo=str(payload["repo"]),
        issue_number=int(payload["issue_number"]),
        issue_url=str(payload["issue_url"]),
        title=str(payload["title"]),
        reward_mrwk=str(payload["reward_mrwk"]),
        max_awards=int(payload["max_awards"]),
        acceptance=str(payload["acceptance"]),
    )
    entry = session.scalar(
        select(LedgerEntry)
        .where(
            LedgerEntry.entry_type == "bounty_reserve",
            LedgerEntry.to_account == reserve_account_for_bounty(bounty.id),
        )
        .order_by(LedgerEntry.sequence.desc())
        .limit(1)
    )
    return {"bounty": bounty_to_dict(bounty)}, entry.sequence if entry else None


def _execute_pay_bounty(session: Session, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    verifier_result = {"source": "treasury_proposal", "accepted_by": payload["accepted_by"]}
    if payload.get("note"):
        verifier_result["note"] = str(payload["note"])
    proof = pay_bounty(
        session,
        bounty_id=int(payload["bounty_id"]),
        to_account=str(payload["to_account"]),
        submission_url=str(payload["submission_url"]),
        accepted_by=str(payload["accepted_by"]),
        verifier_result=verifier_result,
    )
    bounty = session.get(Bounty, int(payload["bounty_id"]))
    if bounty is None:
        raise LedgerError("bounty not found")
    bounty_state = bounty_to_dict(bounty)
    payout_response = _payout_response_from_proof(proof)
    payout_response.update(
        {
            "bounty_status": bounty_state["status"],
            "awards_paid": bounty_state["awards_paid"],
            "awards_remaining": bounty_state["awards_remaining"],
        }
    )
    return {"payout": payout_response}, proof.ledger_sequence


def _execute_close_bounty(
    session: Session, payload: dict[str, Any]
) -> tuple[dict[str, Any], int | None]:
    release = close_bounty(
        session,
        bounty_id=int(payload["bounty_id"]),
        closed_by=str(payload["closed_by"]),
        reference=payload.get("reference"),
    )
    return {
        "close": {
            "status": "closed",
            "bounty_id": int(payload["bounty_id"]),
            "released_mrwk": format_mrwk(release.amount_microunits) if release else "0",
            "ledger_sequence": release.sequence if release else None,
        }
    }, release.sequence if release else None


def execute_treasury_proposal(
    session: Session, *, proposal_id: int, executed_by: str
) -> TreasuryProposal:
    proposal = session.get(TreasuryProposal, proposal_id)
    if proposal is None:
        raise LedgerError("proposal not found")
    if proposal.status == "executed":
        raise LedgerError("proposal already executed")
    if proposal.status == "blocked" or any(
        challenge.status == "accepted_blocking" for challenge in proposal.challenges
    ):
        raise LedgerError("proposal has blocking challenge")
    if proposal.status != "pending":
        raise LedgerError("proposal is not pending")
    now = _db_now()
    if now < _db_utc(proposal.executes_after):
        raise LedgerError("proposal delay has not elapsed")
    payload = _canonical_payload(proposal.action, proposal_payload(proposal))
    if _proposal_hash(proposal.action, payload) != proposal.payload_hash:
        raise LedgerError("proposal payload hash mismatch")
    if proposal.action == "create_bounty":
        result, ledger_sequence = _execute_create_bounty(session, payload, now)
    elif proposal.action == "pay_bounty":
        result, ledger_sequence = _execute_pay_bounty(session, payload)
    elif proposal.action == "close_bounty":
        result, ledger_sequence = _execute_close_bounty(session, payload)
    else:
        raise LedgerError("unsupported treasury action")
    proposal.status = "executed"
    proposal.executed_by = _clean_string(executed_by, "executed_by", 128)
    proposal.executed_at = now
    proposal.executed_ledger_sequence = ledger_sequence
    proposal.result_json = canonical_json(result)
    session.flush()
    return proposal


def has_accepted_work_for_github(session: Session, github_login: str) -> bool:
    normalized = github_login.strip().lower()
    accounts = [f"github:{normalized}"]
    wallet = linked_wallet_for_github(session, normalized)
    if wallet is not None:
        accounts.append(wallet.address)
    exists = session.scalar(
        select(LedgerEntry.sequence)
        .where(LedgerEntry.entry_type == "bounty_payment", LedgerEntry.to_account.in_(accounts))
        .limit(1)
    )
    return exists is not None


def _machine_challenge_is_valid(
    session: Session, proposal: TreasuryProposal, challenge_type: str
) -> bool:
    payload = proposal_payload(proposal)
    if challenge_type == "duplicate_bounty" and proposal.action == "create_bounty":
        existing_bounty = session.scalar(
            select(Bounty)
            .where(
                func.lower(Bounty.repo) == str(payload["repo"]).lower(),
                Bounty.issue_number == int(payload["issue_number"]),
            )
            .limit(1)
        )
        return existing_bounty is not None or _has_pending_create_bounty_proposal(
            session,
            repo=str(payload["repo"]),
            issue_number=int(payload["issue_number"]),
            exclude_proposal_id=proposal.id,
        )
    if challenge_type == "bounty_not_open" and proposal.action in {"pay_bounty", "close_bounty"}:
        bounty = session.get(Bounty, int(payload["bounty_id"]))
        bounty_id = int(payload["bounty_id"])
        if bounty is None or bounty.status != "open":
            return True
        if proposal.action == "pay_bounty":
            return _has_pending_close_bounty_proposal(
                session, bounty_id=bounty_id, exclude_proposal_id=proposal.id
            )
        return _has_pending_close_bounty_proposal(
            session, bounty_id=bounty_id, exclude_proposal_id=proposal.id
        )
    if challenge_type == "submission_already_paid" and proposal.action == "pay_bounty":
        existing_submission = session.scalar(
            select(Submission)
            .where(
                Submission.bounty_id == int(payload["bounty_id"]),
                Submission.url == str(payload["submission_url"]),
            )
            .limit(1)
        )
        return existing_submission is not None or _has_pending_pay_bounty_proposal(
            session,
            bounty_id=int(payload["bounty_id"]),
            submission_url=str(payload["submission_url"]),
            exclude_proposal_id=proposal.id,
        )
    if challenge_type == "insufficient_reserve" and proposal.action == "pay_bounty":
        bounty = session.get(Bounty, int(payload["bounty_id"]))
        if bounty is None:
            return True
        pending_count = len(
            _pending_pay_bounty_payloads(session, bounty.id, through_proposal_id=int(proposal.id))
        )
        return bounty.awards_paid + pending_count > bounty.max_awards or get_balance(
            session, reserve_account_for_bounty(bounty.id)
        ) < (bounty.reward_microunits * pending_count)
    if challenge_type == "epoch_cap_exceeded" and proposal.action == "create_bounty":
        return (
            _epoch_reserved_microunits(session, _db_now())
            + _pending_create_bounty_reserved_microunits(
                session, through_proposal_id=int(proposal.id)
            )
            > TREASURY_EPOCH_RESERVE_CAP_MICRO
        )
    return False


def create_treasury_challenge(
    session: Session,
    *,
    proposal_id: int,
    github_login: str,
    challenge_type: str,
    reason: str,
) -> TreasuryChallenge:
    if not has_accepted_work_for_github(session, github_login):
        raise PermissionError("accepted MRWK work required to challenge proposals")
    proposal = session.get(TreasuryProposal, proposal_id)
    if proposal is None:
        raise LedgerError("proposal not found")
    clean_type = _clean_string(challenge_type, "challenge_type", 80)
    if clean_type not in CHALLENGE_TYPES:
        raise LedgerError("unsupported challenge type")
    clean_reason = _clean_string(reason, "reason", 1_000)
    status = "noted"
    if clean_type in MACHINE_CHALLENGES:
        status = "rejected"
        if proposal.status == "pending" and _machine_challenge_is_valid(
            session, proposal, clean_type
        ):
            status = "accepted_blocking"
        if status == "accepted_blocking":
            proposal.status = "blocked"
    challenge = TreasuryChallenge(
        proposal_id=proposal.id,
        challenger_account=f"github:{github_login.strip().lower()}",
        challenge_type=clean_type,
        status=status,
        reason=clean_reason,
    )
    session.add(challenge)
    session.flush()
    return challenge
