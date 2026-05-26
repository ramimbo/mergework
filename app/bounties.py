from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Bounty

SQLITE_INTEGER_MAX = 2**63 - 1
VALID_BOUNTY_STATUSES = frozenset({"open", "paid", "closed"})


def normalize_bounty_status(status: str | None) -> str | None:
    if status is None:
        return None
    normalized = status.strip().lower()
    if normalized not in VALID_BOUNTY_STATUSES:
        raise ValueError("status must be one of: open, paid, closed")
    return normalized


def issue_number_search_value(query_text: str) -> int | None:
    if not query_text.isdigit():
        return None
    try:
        issue_number = int(query_text)
    except ValueError:
        return None
    return issue_number if issue_number <= SQLITE_INTEGER_MAX else None


def search_bounties(
    session: Session,
    *,
    status: str | None = None,
    query_text: str | None = None,
    limit: int | None = None,
) -> list[Bounty]:
    query = select(Bounty)
    normalized_status = normalize_bounty_status(status)
    if normalized_status is not None:
        query = query.where(Bounty.status == normalized_status)

    normalized_query = query_text.strip() if query_text is not None else ""
    if normalized_query:
        escaped_query = (
            normalized_query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        like_query = f"%{escaped_query}%"
        text_filter = or_(
            func.lower(Bounty.repo).like(like_query, escape="\\"),
            func.lower(Bounty.title).like(like_query, escape="\\"),
            func.lower(Bounty.acceptance).like(like_query, escape="\\"),
        )
        issue_number = issue_number_search_value(normalized_query)
        if issue_number is not None:
            text_filter = or_(text_filter, Bounty.issue_number == issue_number)
        query = query.where(text_filter)

    query = query.order_by(Bounty.id.desc())
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query).all())
