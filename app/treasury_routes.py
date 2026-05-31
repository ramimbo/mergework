from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import select

from app.api_schemas import TreasuryProposalResponse
from app.db import session_scope
from app.ledger.service import LedgerError
from app.models import TreasuryProposal
from app.path_params import SQLITE_INTEGER_MAX
from app.treasury import (
    challenge_to_dict,
    create_treasury_challenge,
    proposal_to_dict,
    propose_treasury_action,
    treasury_status,
)
from app.treasury_executor import execute_treasury_proposal_with_finalization


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
    @app.get(
        "/api/v1/treasury/proposals",
        response_model=list[TreasuryProposalResponse],
        response_model_exclude_unset=True,
    )
    def api_treasury_proposals(
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[dict[str, Any]]:
        with session_scope(db_url) as session:
            proposals = session.scalars(
                select(TreasuryProposal).order_by(TreasuryProposal.id.desc()).limit(limit)
            ).all()
            return [proposal_to_dict(proposal) for proposal in proposals]

    @app.get("/api/v1/treasury/status")
    def api_treasury_status() -> dict[str, Any]:
        with session_scope(db_url) as session:
            return treasury_status(session)

    @app.get(
        "/api/v1/treasury/proposals/{proposal_id}",
        response_model=TreasuryProposalResponse,
        response_model_exclude_unset=True,
    )
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
