from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Bounty, BountyAttempt


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def attempt_effective_status(attempt: BountyAttempt, now: datetime) -> str:
    if attempt.status == "active" and as_utc(attempt.expires_at) <= now:
        return "expired"
    return attempt.status


def bounty_attempt_to_dict(attempt: BountyAttempt, now: datetime) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "bounty_id": attempt.bounty_id,
        "submitter_account": attempt.submitter_account,
        "source_url": attempt.source_url,
        "status": attempt_effective_status(attempt, now),
        "expires_at": as_utc(attempt.expires_at).isoformat(),
        "created_at": as_utc(attempt.created_at).isoformat(),
        "updated_at": as_utc(attempt.updated_at).isoformat(),
    }


def active_attempt_conditions(bounty_id: int, now: datetime) -> tuple[Any, ...]:
    return (
        BountyAttempt.bounty_id == bounty_id,
        BountyAttempt.status == "active",
        BountyAttempt.expires_at > now,
    )


def bounty_attempt_warnings(session: Session, bounty: Bounty, now: datetime) -> list[str]:
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
        .where(*active_attempt_conditions(bounty.id, now))
    )
    if active_count and active_count > 1:
        warnings.append(f"bounty has {active_count} active attempts")
    return warnings


def expire_stale_bounty_attempts(
    session: Session, bounty_id: int, now: datetime, submitter_account: str | None = None
) -> None:
    query = update(BountyAttempt).where(
        BountyAttempt.bounty_id == bounty_id,
        BountyAttempt.status == "active",
        BountyAttempt.expires_at <= now,
    )
    if submitter_account is not None:
        query = query.where(BountyAttempt.submitter_account == submitter_account)
    session.execute(query.values(status="expired", updated_at=now))
