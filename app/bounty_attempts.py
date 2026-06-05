from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.control_chars import contains_control_character
from app.db import session_scope
from app.ledger.service import LedgerError, validate_public_url
from app.models import Bounty, BountyAttempt
from app.openapi_request_bodies import OPTIONAL_ATTEMPT_BODY, OPTIONAL_ATTEMPT_RELEASE_BODY
from app.query_validation import (
    reject_noncanonical_bool_query_param,
    reject_noncanonical_int_query_param,
    reject_repeated_query_param,
)

DEFAULT_ATTEMPT_TTL_SECONDS = 24 * 60 * 60
MIN_ATTEMPT_TTL_SECONDS = 60
MAX_ATTEMPT_TTL_SECONDS = 7 * 24 * 60 * 60

JsonObjectLoader = Callable[[Request], Awaitable[dict[str, Any]]]
LoginDependency = Callable[[Request], str]
RequiredString = Callable[[dict[str, Any], str], str]
OptionalInteger = Callable[[dict[str, Any], str, int], int]
NormalizeAccount = Callable[[str], str]
PositiveBountyId = Callable[[int | str], int]


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


async def _optional_json_object(request: Request, json_object: JsonObjectLoader) -> dict[str, Any]:
    if not (await request.body()).strip():
        return {}
    return await json_object(request)


def bounty_attempt_to_dict(attempt: BountyAttempt, now: datetime | None = None) -> dict[str, Any]:
    now = _as_utc(now or _utc_now())
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


def _active_attempt_conditions(bounty_id: int, now: datetime) -> tuple[Any, ...]:
    return (
        BountyAttempt.bounty_id == bounty_id,
        BountyAttempt.status == "active",
        BountyAttempt.expires_at > now,
    )


def _bounty_attempt_warnings_for_count(
    bounty: Bounty,
    active_count: int,
    *,
    session: Session | None = None,
    pending_proposals: tuple[list[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> list[str]:
    from app.serializers import bounty_to_dict

    warnings: list[str] = []
    bounty_data = bounty_to_dict(
        bounty,
        session=session,
        pending_proposals=pending_proposals,
        attempt_summary={},
    )
    awards_remaining = int(bounty_data["effective_awards_remaining"])
    if bounty_data["status"] != "open":
        warnings.append(f"bounty is {bounty.status}")
    if awards_remaining <= 0:
        warnings.append("bounty has no award slots remaining")
    if bounty_data["availability_state"] not in {"open", "full", bounty_data["status"]}:
        warnings.append(bounty_data["availability_note"])
    if active_count and (
        active_count > 1 or (awards_remaining > 0 and active_count >= awards_remaining)
    ):
        attempt_label = "attempt" if active_count == 1 else "attempts"
        warnings.append(f"bounty has {active_count} active {attempt_label}")
    return warnings


def active_bounty_attempt_count(session: Session, bounty_id: int, now: datetime) -> int:
    active_count = session.scalar(
        select(func.count())
        .select_from(BountyAttempt)
        .where(*_active_attempt_conditions(bounty_id, now))
    )
    return int(active_count or 0)


def active_bounty_attempt_counts(
    session: Session, bounty_ids: Sequence[int], now: datetime
) -> dict[int, int]:
    if not bounty_ids:
        return {}
    rows = session.execute(
        select(BountyAttempt.bounty_id, func.count())
        .where(
            BountyAttempt.bounty_id.in_(bounty_ids),
            BountyAttempt.status == "active",
            BountyAttempt.expires_at > now,
        )
        .group_by(BountyAttempt.bounty_id)
    ).all()
    return {int(bounty_id): int(active_count or 0) for bounty_id, active_count in rows}


def bounty_attempt_warnings(session: Session, bounty: Bounty, now: datetime) -> list[str]:
    return _bounty_attempt_warnings_for_count(
        bounty,
        active_bounty_attempt_count(session, bounty.id, now),
        session=session,
    )


def bounty_attempt_summary_from_count(
    bounty: Bounty,
    active_count: int,
    *,
    session: Session | None = None,
    pending_proposals: tuple[list[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    return {
        "active_attempt_count": active_count,
        "active_attempt_warnings": _bounty_attempt_warnings_for_count(
            bounty,
            active_count,
            session=session,
            pending_proposals=pending_proposals,
        ),
        "attempt_endpoint": f"/api/v1/bounties/{bounty.id}/attempts",
    }


def bounty_attempt_summary(
    session: Session,
    bounty: Bounty,
    now: datetime | None = None,
    *,
    pending_proposals: tuple[list[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    now = _as_utc(now or _utc_now())
    return bounty_attempt_summary_from_count(
        bounty,
        active_bounty_attempt_count(session, bounty.id, now),
        session=session,
        pending_proposals=pending_proposals,
    )


def list_bounty_attempts(
    session: Session,
    bounty: Bounty,
    *,
    include_expired: bool = False,
    limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = _as_utc(now or _utc_now())
    query = select(BountyAttempt).where(BountyAttempt.bounty_id == bounty.id)
    if not include_expired:
        query = query.where(*_active_attempt_conditions(bounty.id, now))
    query = query.order_by(BountyAttempt.created_at.desc(), BountyAttempt.id.desc())
    if limit is not None:
        query = query.limit(limit)
    attempts = session.scalars(query).all()
    return {
        "warnings": bounty_attempt_warnings(session, bounty, now),
        "attempts": [bounty_attempt_to_dict(attempt, now) for attempt in attempts],
    }


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


def _duplicate_active_attempt_response(
    session: Session,
    bounty: Bounty,
    attempt: BountyAttempt,
    now: datetime,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "status": "duplicate_active_attempt",
            "attempt": bounty_attempt_to_dict(attempt, now),
            "warnings": bounty_attempt_warnings(session, bounty, now),
        },
    )


def register_bounty_attempt_routes(
    app: FastAPI,
    *,
    db_url: str,
    require_github_login: LoginDependency,
    json_object: JsonObjectLoader,
    required_str: RequiredString,
    optional_int: OptionalInteger,
    normalized_account: NormalizeAccount,
    positive_bounty_id: PositiveBountyId,
    sqlite_integer_max: int,
) -> None:
    def attempt_submitter_account(data: dict[str, Any], github_login: str) -> str:
        submitter_account = f"github:{github_login}"
        if data.get("submitter_account") is None:
            return submitter_account
        requested_account = normalized_account(required_str(data, "submitter_account"))
        if requested_account != submitter_account:
            raise HTTPException(status_code=403, detail="submitter_account does not match login")
        return submitter_account

    @app.get("/api/v1/bounties/{bounty_id}/attempts")
    def api_bounty_attempts(
        request: Request,
        bounty_id: str,
        include_expired: str | None = Query(None),
        limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    ) -> dict[str, Any]:
        for name in ("include_expired", "limit"):
            reject_repeated_query_param(request, name)
        reject_noncanonical_bool_query_param(request, "include_expired")
        reject_noncanonical_int_query_param(request, "limit")
        bounty_id_int = positive_bounty_id(bounty_id)
        now = _utc_now()
        with session_scope(db_url) as session:
            bounty = session.get(Bounty, bounty_id_int)
            if bounty is None:
                raise HTTPException(status_code=404, detail="bounty not found")
            listing = list_bounty_attempts(
                session,
                bounty,
                include_expired=include_expired == "true",
                limit=limit,
                now=now,
            )
            return {
                "bounty_id": bounty_id_int,
                "warnings": listing["warnings"],
                "attempts": listing["attempts"],
            }

    @app.post("/api/v1/bounties/{bounty_id}/attempts", openapi_extra=OPTIONAL_ATTEMPT_BODY)
    async def api_create_bounty_attempt(
        bounty_id: str,
        request: Request,
        github_login: str = Depends(require_github_login),
    ) -> JSONResponse:
        bounty_id_int = positive_bounty_id(bounty_id)
        data = await _optional_json_object(request, json_object)
        submitter_account = attempt_submitter_account(data, github_login)
        ttl_seconds = optional_int(data, "ttl_seconds", DEFAULT_ATTEMPT_TTL_SECONDS)
        if ttl_seconds < MIN_ATTEMPT_TTL_SECONDS:
            raise HTTPException(status_code=400, detail="ttl_seconds must be at least 60")
        if ttl_seconds > MAX_ATTEMPT_TTL_SECONDS:
            raise HTTPException(status_code=400, detail="ttl_seconds must be no more than 604800")
        source = data.get("source_url", "")
        if source is None:
            source = ""
        if not isinstance(source, str):
            raise HTTPException(status_code=400, detail="source_url must be a string")
        if contains_control_character(source):
            raise HTTPException(
                status_code=400, detail="source_url must not contain control characters"
            )
        source = source.strip()
        try:
            source_url = validate_public_url(source) if source else None
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        now = _utc_now()
        with session_scope(db_url) as session:
            bounty = session.get(Bounty, bounty_id_int)
            if bounty is None:
                raise HTTPException(status_code=404, detail="bounty not found")
            expire_stale_bounty_attempts(session, bounty_id_int, now, submitter_account)
            from app.serializers import bounty_to_dict

            bounty_data = bounty_to_dict(bounty, session=session, attempt_summary={})
            awards_remaining = int(bounty_data["effective_awards_remaining"])
            if bounty.status != "open" or awards_remaining <= 0:
                return JSONResponse(
                    status_code=409,
                    content={
                        "status": "not_available",
                        "bounty_id": bounty_id_int,
                        "warnings": bounty_attempt_warnings(session, bounty, now),
                    },
                )
            existing = session.scalar(
                select(BountyAttempt)
                .where(
                    *_active_attempt_conditions(bounty_id_int, now),
                    BountyAttempt.submitter_account == submitter_account,
                )
                .order_by(BountyAttempt.created_at.desc(), BountyAttempt.id.desc())
                .limit(1)
            )
            if existing is not None:
                return _duplicate_active_attempt_response(session, bounty, existing, now)
            attempt = BountyAttempt(
                bounty_id=bounty_id_int,
                submitter_account=submitter_account,
                source_url=source_url,
                status="active",
                expires_at=now + timedelta(seconds=ttl_seconds),
                created_at=now,
                updated_at=now,
            )
            session.add(attempt)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                bounty = session.get(Bounty, bounty_id_int)
                existing = session.scalar(
                    select(BountyAttempt)
                    .where(
                        *_active_attempt_conditions(bounty_id_int, now),
                        BountyAttempt.submitter_account == submitter_account,
                    )
                    .order_by(BountyAttempt.created_at.desc(), BountyAttempt.id.desc())
                    .limit(1)
                )
                if bounty is None or existing is None:
                    raise HTTPException(
                        status_code=409, detail="active attempt already exists"
                    ) from None
                return _duplicate_active_attempt_response(session, bounty, existing, now)
            return JSONResponse(
                status_code=201,
                content={
                    "status": "registered",
                    "attempt": bounty_attempt_to_dict(attempt, now),
                    "warnings": bounty_attempt_warnings(session, bounty, now),
                },
            )

    @app.post(
        "/api/v1/bounty-attempts/{attempt_id}/release",
        openapi_extra=OPTIONAL_ATTEMPT_RELEASE_BODY,
    )
    async def api_release_bounty_attempt(
        attempt_id: int,
        request: Request,
        github_login: str = Depends(require_github_login),
    ) -> dict[str, Any]:
        if attempt_id <= 0:
            raise HTTPException(status_code=400, detail="attempt id must be positive")
        if attempt_id > sqlite_integer_max:
            raise HTTPException(status_code=400, detail="attempt id is too large")
        data = await _optional_json_object(request, json_object)
        submitter_account = attempt_submitter_account(data, github_login)
        now = _utc_now()
        with session_scope(db_url) as session:
            attempt = session.get(BountyAttempt, attempt_id)
            if attempt is None:
                raise HTTPException(status_code=404, detail="attempt not found")
            if attempt.submitter_account != submitter_account:
                raise HTTPException(status_code=403, detail="submitter_account does not match")
            effective_status = _attempt_effective_status(attempt, now)
            if effective_status != "active":
                return {
                    "status": f"already_{effective_status}",
                    "attempt": bounty_attempt_to_dict(attempt, now),
                }
            attempt.status = "released"
            attempt.updated_at = now
            session.flush()
            return {
                "status": "released",
                "attempt": bounty_attempt_to_dict(attempt, now),
            }
