from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Bounty, TreasuryProposal
from app.path_params import issue_number_search_value
from app.serializers import bounties_to_dict, public_utc_timestamp
from app.submission_requirements import work_proof_submission_requirements
from app.treasury import proposal_payload

DEFAULT_WORK_DISCOVERY_LIMIT = 50
MAX_WORK_DISCOVERY_LIMIT = 100
OPEN_BOUNTY_SCAN_PAGE_SIZE = 25
MAX_OPEN_BOUNTY_SCAN_ROWS = 500
WORK_DISCOVERY_REPO_FILTER_MAX_LENGTH = 200
WORK_DISCOVERY_SEARCH_QUERY_MAX_LENGTH = 500

STATE_DEFINITIONS = {
    "live_bounty": "Public bounty row is open and has positive effective_awards_remaining.",
    "pending_create": "Public treasury proposal exists but the bounty row is not live yet.",
    "pending_payout": "Accepted work has a pending pay_bounty proposal, not proof-backed payment.",
    "closed_or_exhausted": "Bounty is closed, paid, or has no effective award capacity.",
    "proposed_work": (
        "GitHub proposed-work issue is intake only until a create_bounty proposal executes."
    ),
    "board_or_index": "Index issues help discovery but are not claimable bounty work.",
}


NON_CLAIMABLE_ISSUE_STATES = [
    {
        "availability_state": "proposed_work",
        "note": STATE_DEFINITIONS["proposed_work"],
    },
    {
        "availability_state": "board_or_index",
        "repo": "ramimbo/mergework",
        "issue_number": 785,
        "issue_url": "https://github.com/ramimbo/mergework/issues/785",
        "title": "MRWK bounty board",
        "note": STATE_DEFINITIONS["board_or_index"],
    },
]


def _bounty_source_urls(row: dict[str, Any]) -> dict[str, str]:
    bounty_id = int(row["id"])
    return {
        "bounty": f"/api/v1/bounties/{bounty_id}",
        "attempts": f"/api/v1/bounties/{bounty_id}/attempts",
        "github_issue": str(row["issue_url"]),
    }


def _next_action(requirements: dict[str, Any]) -> dict[str, Any]:
    actions = requirements.get("next_actions")
    if not isinstance(actions, list) or not actions:
        return {
            "id": "review_submission_requirements",
            "required": True,
            "text": "Review submission requirements before opening or claiming work.",
        }
    action = actions[0]
    if not isinstance(action, dict):
        return {
            "id": "review_submission_requirements",
            "required": True,
            "text": "Review submission requirements before opening or claiming work.",
        }
    return action


def _normalized_text_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalized_repo_filter(value: str | None) -> str | None:
    normalized = _normalized_text_filter(value)
    if normalized is None:
        return None
    return normalized.lower()


def _query_like_value(value: str) -> str:
    escaped = value.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _filtered_bounty_query(
    *,
    query_text: str | None,
    repo: str | None,
    issue_number: int | None,
) -> Any:
    query = select(Bounty)
    normalized_query = _normalized_text_filter(query_text)
    if normalized_query is not None:
        like_query = _query_like_value(normalized_query)
        query_issue_number = issue_number_search_value(normalized_query)
        text_filter = or_(
            func.lower(Bounty.repo).like(like_query, escape="\\"),
            func.lower(Bounty.title).like(like_query, escape="\\"),
            func.lower(Bounty.acceptance).like(like_query, escape="\\"),
        )
        if query_issue_number is not None:
            text_filter = or_(text_filter, Bounty.issue_number == query_issue_number)
        query = query.where(text_filter)
    normalized_repo = _normalized_repo_filter(repo)
    if normalized_repo is not None:
        query = query.where(func.lower(Bounty.repo) == normalized_repo)
    if issue_number is not None:
        query = query.where(Bounty.issue_number == issue_number)
    return query


def _payload_matches_filters(
    payload: dict[str, Any],
    *,
    query_text: str | None,
    repo: str | None,
    issue_number: int | None,
) -> bool:
    if issue_number is not None and int(payload["issue_number"]) != issue_number:
        return False
    normalized_repo = _normalized_repo_filter(repo)
    if normalized_repo is not None and str(payload.get("repo", "")).lower() != normalized_repo:
        return False
    normalized_query = _normalized_text_filter(query_text)
    if normalized_query is None:
        return True
    query_issue_number = issue_number_search_value(normalized_query)
    if query_issue_number is not None and int(payload["issue_number"]) == query_issue_number:
        return True
    needle = normalized_query.lower()
    return any(
        needle in str(payload.get(field, "")).lower() for field in ("repo", "title", "acceptance")
    )


def _bounty_work_item(row: dict[str, Any], availability_state: str) -> dict[str, Any]:
    submission_requirements = row["submission_requirements"]
    return {
        "availability_state": availability_state,
        "bounty_id": int(row["id"]),
        "repo": str(row["repo"]),
        "issue_number": int(row["issue_number"]),
        "title": str(row["title"]),
        "issue_url": str(row["issue_url"]),
        "reward_mrwk": str(row["reward_mrwk"]),
        "max_awards": int(row["max_awards"]),
        "effective_awards_remaining": int(row["effective_awards_remaining"]),
        "bounty_availability_state": str(row["availability_state"]),
        "pending_payout_awards": int(row["pending_payout_awards"]),
        "source_urls": _bounty_source_urls(row),
        "next_action": _next_action(submission_requirements),
        "submission_requirements": submission_requirements,
    }


def _not_claimable_state(row: dict[str, Any]) -> str:
    if int(row["pending_payout_awards"]) > 0 and int(row["effective_awards_remaining"]) <= 0:
        return "pending_payout"
    return "closed_or_exhausted"


def _pending_create_item(proposal: TreasuryProposal) -> dict[str, Any]:
    payload = proposal_payload(proposal)
    submission_requirements = work_proof_submission_requirements(
        bounty_id=None,
        issue_number=int(payload["issue_number"]),
        availability="unknown",
        title=str(payload["title"]),
        acceptance=str(payload.get("acceptance", "")),
    )
    return {
        "availability_state": "pending_create",
        "proposal_id": int(proposal.id),
        "repo": str(payload["repo"]),
        "issue_number": int(payload["issue_number"]),
        "title": str(payload["title"]),
        "issue_url": str(payload["issue_url"]),
        "reward_mrwk": str(payload["reward_mrwk"]),
        "max_awards": int(payload["max_awards"]),
        "effective_awards_remaining": 0,
        "executes_after": public_utc_timestamp(proposal.executes_after),
        "source_urls": {
            "proposal": f"/api/v1/treasury/proposals/{proposal.id}",
            "github_issue": str(payload["issue_url"]),
        },
        "next_action": _next_action(submission_requirements),
        "submission_requirements": submission_requirements,
    }


def _pending_create_matches_filters(
    proposal: TreasuryProposal,
    *,
    query_text: str | None,
    repo: str | None,
    issue_number: int | None,
) -> bool:
    return _payload_matches_filters(
        proposal_payload(proposal),
        query_text=query_text,
        repo=repo,
        issue_number=issue_number,
    )


def _append_capped_item(bucket: list[dict[str, Any]], item: dict[str, Any], *, limit: int) -> None:
    if len(bucket) < limit:
        bucket.append(item)


def _scan_open_bounty_buckets(
    session: Session,
    *,
    limit: int,
    query_text: str | None,
    repo: str | None,
    issue_number: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    claimable_now: list[dict[str, Any]] = []
    not_claimable: list[dict[str, Any]] = []
    last_seen_id: int | None = None
    scanned_rows = 0
    page_size = min(MAX_WORK_DISCOVERY_LIMIT, max(limit, OPEN_BOUNTY_SCAN_PAGE_SIZE))

    while scanned_rows < MAX_OPEN_BOUNTY_SCAN_ROWS:
        batch_limit = min(page_size, MAX_OPEN_BOUNTY_SCAN_ROWS - scanned_rows)
        query = _filtered_bounty_query(
            query_text=query_text,
            repo=repo,
            issue_number=issue_number,
        ).where(Bounty.status == "open")
        if last_seen_id is not None:
            query = query.where(Bounty.id < last_seen_id)
        batch = session.scalars(query.order_by(Bounty.id.desc()).limit(batch_limit)).all()
        if not batch:
            break

        rows = bounties_to_dict(batch, session=session)
        scanned_rows += len(rows)
        last_seen_id = int(batch[-1].id)
        for row in rows:
            if int(row["effective_awards_remaining"]) > 0:
                _append_capped_item(
                    claimable_now,
                    _bounty_work_item(row, "live_bounty"),
                    limit=limit,
                )
            else:
                _append_capped_item(
                    not_claimable,
                    _bounty_work_item(row, _not_claimable_state(row)),
                    limit=limit,
                )

        if len(batch) < batch_limit:
            break
        if len(claimable_now) >= limit and len(not_claimable) >= limit:
            break

    return claimable_now, not_claimable


def work_discovery_to_dict(
    session: Session,
    *,
    limit: int = DEFAULT_WORK_DISCOVERY_LIMIT,
    query_text: str | None = None,
    repo: str | None = None,
    issue_number: int | None = None,
) -> dict[str, Any]:
    """Return public read-only work discovery grouped by claimability."""
    capped_limit = max(1, min(limit, MAX_WORK_DISCOVERY_LIMIT))
    normalized_query = _normalized_text_filter(query_text)
    normalized_repo = _normalized_repo_filter(repo)
    claimable_now, not_claimable = _scan_open_bounty_buckets(
        session,
        limit=capped_limit,
        query_text=normalized_query,
        repo=normalized_repo,
        issue_number=issue_number,
    )

    if len(not_claimable) < capped_limit:
        remaining_not_claimable = capped_limit - len(not_claimable)
        terminal_bounties = session.scalars(
            _filtered_bounty_query(
                query_text=normalized_query,
                repo=normalized_repo,
                issue_number=issue_number,
            )
            .where(Bounty.status != "open")
            .order_by(Bounty.id.desc())
            .limit(remaining_not_claimable)
        ).all()
        terminal_rows = bounties_to_dict(terminal_bounties, session=session)
        for row in terminal_rows:
            _append_capped_item(
                not_claimable,
                _bounty_work_item(row, _not_claimable_state(row)),
                limit=capped_limit,
            )

    pending_create_proposals = session.scalars(
        select(TreasuryProposal)
        .where(TreasuryProposal.status == "pending", TreasuryProposal.action == "create_bounty")
        .order_by(TreasuryProposal.executes_after.asc(), TreasuryProposal.id.asc())
        .limit(MAX_OPEN_BOUNTY_SCAN_ROWS)
    ).all()
    opening_soon: list[dict[str, Any]] = []
    for proposal in pending_create_proposals:
        if not _pending_create_matches_filters(
            proposal,
            query_text=normalized_query,
            repo=normalized_repo,
            issue_number=issue_number,
        ):
            continue
        _append_capped_item(opening_soon, _pending_create_item(proposal), limit=capped_limit)
        if len(opening_soon) >= capped_limit:
            break

    return {
        "type": "work_discovery",
        "summary": {
            "claimable_now_count": len(claimable_now),
            "opening_soon_count": len(opening_soon),
            "not_claimable_count": len(not_claimable),
            "limit": capped_limit,
        },
        "filters": {
            "q": normalized_query,
            "repo": normalized_repo,
            "issue_number": issue_number,
        },
        "state_definitions": STATE_DEFINITIONS,
        "claimable_now": claimable_now,
        "opening_soon": opening_soon,
        "not_claimable": not_claimable,
        "non_claimable_issue_states": NON_CLAIMABLE_ISSUE_STATES,
    }
