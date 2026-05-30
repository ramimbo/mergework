from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import select

from app.db import session_scope
from app.ledger.service import LedgerError
from app.models import TreasuryProposal
from app.path_params import SQLITE_INTEGER_MAX
from app.treasury import (
    TREASURY_ACTIONS,
    challenge_to_dict,
    create_treasury_challenge,
    execute_treasury_proposal,
    proposal_to_dict,
    propose_treasury_action,
)

TREASURY_PROPOSAL_STATUSES = {"pending", "executed", "blocked"}


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


def _normalized_filter(value: str | None, field: str, allowed: set[str]) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise HTTPException(status_code=400, detail=f"{field} must be one of: {allowed_values}")
    return normalized


def register_treasury_routes(
    app: FastAPI,
    *,
    db_url: str,
    require_admin_token: Any,
    require_github_login: Any,
    json_object: Any,
) -> None:
    @app.get("/api/v1/treasury/proposals")
    def api_treasury_proposals(
        status: str | None = Query(None),
        action: str | None = Query(None),
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[dict[str, Any]]:
        normalized_status = _normalized_filter(status, "status", TREASURY_PROPOSAL_STATUSES)
        normalized_action = _normalized_filter(action, "action", TREASURY_ACTIONS)
        with session_scope(db_url) as session:
            query = select(TreasuryProposal)
            if normalized_status is not None:
                query = query.where(TreasuryProposal.status == normalized_status)
            if normalized_action is not None:
                query = query.where(TreasuryProposal.action == normalized_action)
            proposals = session.scalars(
                query.order_by(TreasuryProposal.id.desc()).limit(limit)
            ).all()
            return [proposal_to_dict(proposal) for proposal in proposals]

    @app.get("/api/v1/treasury/proposals/{proposal_id}")
    def api_treasury_proposal(proposal_id: int) -> dict[str, Any]:
        proposal_id = _positive_proposal_id(proposal_id)
        with session_scope(db_url) as session:
            proposal = session.get(TreasuryProposal, proposal_id)
            if proposal is None:
                raise HTTPException(status_code=404, detail="proposal not found")
            return proposal_to_dict(proposal)

    @app.post("/api/v1/treasury/proposals")
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
        with session_scope(db_url) as session:
            try:
                proposal = execute_treasury_proposal(
                    session, proposal_id=proposal_id, executed_by=admin_login
                )
                return proposal_to_dict(proposal)
            except LedgerError as exc:
                raise _proposal_error(exc) from exc

    @app.post("/api/v1/treasury/proposals/{proposal_id}/challenges")
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
