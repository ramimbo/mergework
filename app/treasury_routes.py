from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import select

from app.accounts import normalized_account
from app.control_chars import contains_control_character
from app.db import session_scope
from app.ledger.service import LedgerError
from app.models import TreasuryProposal
from app.openapi_request_bodies import TREASURY_CHALLENGE_BODY, TREASURY_PROPOSAL_BODY
from app.path_params import SQLITE_INTEGER_MAX
from app.treasury import (
    TREASURY_ACTIONS,
    challenge_to_dict,
    create_treasury_challenge,
    proposal_to_dict,
    propose_treasury_action,
    treasury_status,
)
from app.treasury_executor import execute_treasury_proposal_with_finalization

TREASURY_PROPOSAL_STATUSES = ("pending", "executed", "blocked")


def _positive_proposal_id(proposal_id: int) -> int:
    if proposal_id <= 0:
        raise HTTPException(status_code=400, detail="proposal id must be positive")
    if proposal_id > SQLITE_INTEGER_MAX:
        raise HTTPException(status_code=400, detail="proposal id is too large")
    return proposal_id


def _proposal_error(exc: LedgerError) -> HTTPException:
    detail = str(exc)
    if detail in {"proposal not found", "bounty not found"}:
        return HTTPException(status_code=404, detail=detail)
    if detail in {"proposal already executed", "submission already paid"}:
        return HTTPException(status_code=409, detail=detail)
    return HTTPException(status_code=400, detail=detail)


def _optional_query_filter(
    value: str | None,
    field: str,
    *,
    max_length: int,
    blank_detail: str | None = None,
    allowed_values: tuple[str, ...] | None = None,
    lower: bool = False,
) -> str | None:
    if value is None:
        return None
    if contains_control_character(value):
        raise HTTPException(status_code=400, detail=f"{field} must not contain control characters")
    clean = value.strip()
    if not clean:
        raise HTTPException(status_code=400, detail=blank_detail or f"{field} is required")
    if lower:
        clean = clean.lower()
    if len(clean) > max_length:
        raise HTTPException(status_code=400, detail=f"{field} is too long")
    if allowed_values is not None and clean not in allowed_values:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be one of: {', '.join(allowed_values)}",
        )
    return clean


def _proposal_payload_object(proposal: TreasuryProposal) -> dict[str, Any] | None:
    try:
        payload = json.loads(proposal.payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _proposal_payload_matches(
    proposal: TreasuryProposal,
    *,
    to_account: str | None,
    bounty_id: int | None,
) -> bool:
    if to_account is None and bounty_id is None:
        return True
    payload = _proposal_payload_object(proposal)
    if payload is None:
        return False
    if to_account is not None and payload.get("to_account") != to_account:
        return False
    if bounty_id is None:
        return True
    payload_bounty_id = payload.get("bounty_id")
    return (
        not isinstance(payload_bounty_id, bool)
        and isinstance(payload_bounty_id, int)
        and payload_bounty_id == bounty_id
    )


def register_treasury_routes(
    app: FastAPI,
    *,
    db_url: str,
    github_issue_token: str,
    public_base_url: str,
    require_admin_token: Any,
    require_github_login: Any,
    json_object: Any,
) -> None:
    @app.get("/api/v1/treasury/proposals")
    def api_treasury_proposals(
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        action: Annotated[str | None, Query(max_length=40)] = None,
        status: Annotated[str | None, Query(max_length=40)] = None,
        to_account: Annotated[str | None, Query(max_length=128)] = None,
        bounty_id: Annotated[int | None, Query(ge=1, le=SQLITE_INTEGER_MAX)] = None,
    ) -> list[dict[str, Any]]:
        action_filter = _optional_query_filter(
            action,
            "action",
            max_length=40,
            allowed_values=tuple(sorted(TREASURY_ACTIONS)),
            lower=True,
        )
        status_filter = _optional_query_filter(
            status,
            "status",
            max_length=40,
            allowed_values=TREASURY_PROPOSAL_STATUSES,
            lower=True,
        )
        to_account_filter = _optional_query_filter(
            to_account,
            "to_account",
            max_length=128,
            blank_detail="to_account must not be blank",
        )
        if to_account_filter is not None:
            to_account_filter = normalized_account(to_account_filter)
        with session_scope(db_url) as session:
            query = select(TreasuryProposal).order_by(TreasuryProposal.id.desc())
            if action_filter is not None:
                query = query.where(TreasuryProposal.action == action_filter)
            if status_filter is not None:
                query = query.where(TreasuryProposal.status == status_filter)
            if to_account_filter is None and bounty_id is None:
                query = query.limit(limit)

            proposals: list[dict[str, Any]] = []
            for proposal in session.scalars(query):
                if not _proposal_payload_matches(
                    proposal,
                    to_account=to_account_filter,
                    bounty_id=bounty_id,
                ):
                    continue
                proposals.append(proposal_to_dict(proposal))
                if len(proposals) >= limit:
                    break
            return proposals

    @app.get("/api/v1/treasury/status")
    def api_treasury_status() -> dict[str, Any]:
        with session_scope(db_url) as session:
            return treasury_status(session)

    @app.get("/api/v1/treasury/proposals/{proposal_id}")
    def api_treasury_proposal(proposal_id: int) -> dict[str, Any]:
        proposal_id = _positive_proposal_id(proposal_id)
        with session_scope(db_url) as session:
            proposal = session.get(TreasuryProposal, proposal_id)
            if proposal is None:
                raise HTTPException(status_code=404, detail="proposal not found")
            return proposal_to_dict(proposal)

    @app.post("/api/v1/treasury/proposals", openapi_extra=TREASURY_PROPOSAL_BODY)
    async def api_create_treasury_proposal(
        request: Request,
        admin_login: str = Depends(require_admin_token),
    ) -> dict[str, Any]:
        data = await json_object(request)
        action = data.get("action")
        payload = data.get("payload")
        if not isinstance(action, str):
            raise HTTPException(status_code=400, detail="action must be a string")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="payload must be an object")
        with session_scope(db_url) as session:
            try:
                proposal = propose_treasury_action(
                    session,
                    action=action,
                    payload=payload,
                    proposed_by=admin_login,
                )
                return proposal_to_dict(proposal)
            except LedgerError as exc:
                raise _proposal_error(exc) from exc

    @app.post("/api/v1/treasury/proposals/{proposal_id}/execute")
    def api_execute_treasury_proposal(
        proposal_id: int,
        admin_login: str = Depends(require_admin_token),
    ) -> dict[str, Any]:
        proposal_id = _positive_proposal_id(proposal_id)
        try:
            return execute_treasury_proposal_with_finalization(
                db_url,
                proposal_id=proposal_id,
                executed_by=admin_login,
                github_issue_token=github_issue_token,
                public_base_url=public_base_url,
            )
        except LedgerError as exc:
            raise _proposal_error(exc) from exc

    @app.post(
        "/api/v1/treasury/proposals/{proposal_id}/challenges",
        openapi_extra=TREASURY_CHALLENGE_BODY,
    )
    async def api_create_treasury_challenge(
        proposal_id: int,
        request: Request,
        github_login: str = Depends(require_github_login),
    ) -> dict[str, Any]:
        proposal_id = _positive_proposal_id(proposal_id)
        data = await json_object(request)
        challenge_type = data.get("challenge_type")
        reason = data.get("reason")
        if not isinstance(challenge_type, str):
            raise HTTPException(status_code=400, detail="challenge_type must be a string")
        if not isinstance(reason, str):
            raise HTTPException(status_code=400, detail="reason must be a string")
        with session_scope(db_url) as session:
            try:
                challenge = create_treasury_challenge(
                    session,
                    proposal_id=proposal_id,
                    github_login=github_login,
                    challenge_type=challenge_type,
                    reason=reason,
                )
                return challenge_to_dict(challenge)
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except LedgerError as exc:
                raise _proposal_error(exc) from exc
