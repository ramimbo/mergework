"""Bounty API routes — listing, creation, payment, close, and reconciliation."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.admin import list_webhook_events, webhook_events_to_dict
from app.bounty_sorting import BOUNTY_SORT_ERROR, normalize_bounty_sort, sort_bounties
from app.config import Settings
from app.db import session_scope
from app.ledger.reconciliation import payout_reconciliation_summary, reconcile_accepted_payouts
from app.ledger.service import (
    CONTROL_CHAR_RE,
    LedgerError,
    validate_public_url,
)
from app.models import Bounty, Proof, Submission
from app.path_params import issue_number_search_value, positive_bounty_id
from app.serializers import (
    bounty_awards_to_dict,
    bounty_list_summary,
    bounty_to_dict,
    payout_reconciliation_to_dict,
)
from app.treasury import proposal_to_dict, propose_treasury_action


def _payout_response_from_proof(proof: Proof, *, status: str) -> dict[str, Any]:
    data = json.loads(proof.public_json)
    if not isinstance(data, dict) or data.get("kind") != "bounty_payment":
        raise HTTPException(status_code=500, detail="invalid proof payload")
    return {
        "status": status,
        "bounty_id": proof.bounty_id,
        "to_account": data.get("to_account"),
        "submission_id": proof.submission_id,
        "submission_url": data.get("submission_url"),
        "ledger_sequence": proof.ledger_sequence,
        "ledger_url": f"/ledger/{proof.ledger_sequence}",
        "proof_hash": proof.hash,
        "proof_url": f"/proofs/{proof.hash}",
    }


def _existing_payout_proof_for_submission(
    session: Session, bounty_id: int, submission_url: str
) -> Proof | None:
    submission = session.scalar(
        select(Submission)
        .where(Submission.bounty_id == bounty_id, Submission.url == submission_url)
        .limit(1)
    )
    if submission is None:
        return None
    return session.scalar(
        select(Proof)
        .where(Proof.submission_id == submission.id, Proof.kind == "bounty_payment")
        .limit(1)
    )


def _ledger_http_error(exc: LedgerError) -> HTTPException:
    detail = str(exc)
    if detail == "bounty not found":
        return HTTPException(status_code=404, detail=detail)
    if detail in {
        "submission already paid",
        "create_bounty proposal already pending",
        "pay_bounty proposal already pending for submission",
        "close_bounty proposal already pending",
        "bounty has pending close proposal",
        "bounty has pending payout proposals",
    }:
        return HTTPException(status_code=409, detail=detail)
    return HTTPException(status_code=400, detail=detail)


def register_bounty_api_routes(
    app: FastAPI,
    *,
    db_url: str,
    require_admin_token: Any,
    json_object: Any,
    required_str: Any,
    optional_str: Any,
    optional_int: Any,
    required_int: Any,
    settings: Settings,
) -> dict[str, Any]:
    """Register bounty listing, CRUD, payment, close, and reconciliation routes."""

    def _list_bounties_by_status(
        status: str | None = None,
        query_text: str | None = None,
        sort: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        try:
            normalized_sort = normalize_bounty_sort(sort)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=BOUNTY_SORT_ERROR) from exc
        with session_scope(db_url) as session:
            query = select(Bounty)
            if status is not None:
                normalized_status = status.strip().lower()
                if normalized_status not in {"open", "paid", "closed"}:
                    raise HTTPException(
                        status_code=400, detail="status must be one of: open, paid, closed"
                    )
                query = query.where(Bounty.status == normalized_status)
            if query_text is not None:
                normalized_query = query_text.strip()
                if normalized_query:
                    escaped_query = (
                        normalized_query.lower()
                        .replace("\\", "\\\\")
                        .replace("%", "\\%")
                        .replace("_", "\\_")
                    )
                    like_query = f"%{escaped_query}%"
                    issue_number = issue_number_search_value(normalized_query)
                    text_filter = or_(
                        func.lower(Bounty.repo).like(like_query, escape="\\"),
                        func.lower(Bounty.title).like(like_query, escape="\\"),
                        func.lower(Bounty.acceptance).like(like_query, escape="\\"),
                    )
                    if issue_number is not None:
                        text_filter = or_(text_filter, Bounty.issue_number == issue_number)
                    query = query.where(text_filter)
            bounties = session.scalars(query.order_by(Bounty.id.desc())).all()
            sorted_bounties = sort_bounties(
                [bounty_to_dict(bounty) for bounty in bounties], normalized_sort
            )
            if limit is not None:
                return sorted_bounties[:limit]
            return sorted_bounties

    @app.get("/api/v1/bounties")
    def api_bounties(
        status: str | None = Query(None),
        q: str | None = Query(None),
        limit: Annotated[int | None, Query(ge=1, le=200)] = None,
        sort: str | None = Query(None),
    ) -> list[dict[str, Any]]:
        return _list_bounties_by_status(status, q, sort=sort, limit=limit)

    @app.get("/api/v1/bounties/summary")
    def api_bounties_summary(
        status: str | None = Query(None),
        q: str | None = Query(None),
        limit: Annotated[int | None, Query(ge=1, le=200)] = None,
        sort: str | None = Query(None),
    ) -> dict[str, Any]:
        return bounty_list_summary(_list_bounties_by_status(status, q, sort=sort, limit=limit))

    @app.get("/api/v1/admin/webhook-events")
    def api_admin_webhook_events(
        status: str | None = Query(None),
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        admin_login: str = Depends(require_admin_token),
    ) -> list[dict[str, Any]]:
        del admin_login
        with session_scope(db_url) as session:
            return webhook_events_to_dict(list_webhook_events(session, status, limit))

    @app.post("/api/v1/bounties")
    async def api_create_bounty(
        request: Request, admin_login: str = Depends(require_admin_token)
    ) -> dict[str, Any]:
        data = await json_object(request)
        with session_scope(db_url) as session:
            try:
                proposal = propose_treasury_action(
                    session,
                    action="create_bounty",
                    payload={
                        "repo": required_str(data, "repo"),
                        "issue_number": required_int(data, "issue_number"),
                        "issue_url": required_str(data, "issue_url"),
                        "title": required_str(data, "title"),
                        "reward_mrwk": str(data["reward_mrwk"]),
                        "max_awards": optional_int(data, "max_awards", 1),
                        "acceptance": required_str(data, "acceptance"),
                    },
                    proposed_by=admin_login,
                )
            except KeyError as exc:
                raise HTTPException(status_code=400, detail=f"{exc.args[0]} is required") from exc
            except LedgerError as exc:
                raise _ledger_http_error(exc) from exc
            return proposal_to_dict(proposal)

    @app.get("/api/v1/bounties/{bounty_id}")
    def api_bounty(bounty_id: int) -> dict[str, Any]:
        bounty_id = positive_bounty_id(bounty_id)
        with session_scope(db_url) as session:
            bounty = session.get(Bounty, bounty_id)
            if bounty is None:
                raise HTTPException(status_code=404, detail="bounty not found")
            result = bounty_to_dict(bounty)
            result["accepted_awards"] = bounty_awards_to_dict(session, bounty.id)
            return result

    @app.get("/api/v1/reconciliation/payouts")
    def api_payout_reconciliation(
        admin_login: str = Depends(require_admin_token),
    ) -> dict[str, Any]:
        with session_scope(db_url) as session:
            checks = reconcile_accepted_payouts(session)
            return {
                "generated_by": admin_login,
                "summary": payout_reconciliation_summary(checks),
                "checks": [payout_reconciliation_to_dict(check) for check in checks],
            }

    @app.post("/api/v1/bounties/{bounty_id}/pay")
    async def api_pay_bounty(
        bounty_id: int,
        request: Request,
        admin_login: str = Depends(require_admin_token),
    ) -> Any:
        bounty_id = positive_bounty_id(bounty_id)
        data = await json_object(request)
        try:
            requested_account = required_str(data, "to_account")
            submission_url = required_str(data, "submission_url")
            clean_submission_url = validate_public_url(submission_url)
        except HTTPException as exc:
            if str(exc.detail).endswith(" is required"):
                field = str(exc.detail).removesuffix(" is required")
                raise HTTPException(
                    status_code=400, detail=f"missing required field: {field}"
                ) from exc
            raise
        except LedgerError as exc:
            raise _ledger_http_error(exc) from exc
        accepted_by = optional_str(data, "accepted_by", admin_login) or admin_login
        verifier_result = {
            "source": "admin_api",
            "accepted_by": accepted_by,
        }
        if data.get("note") is not None:
            note = optional_str(data, "note").strip()
            if note:
                if CONTROL_CHAR_RE.search(note):
                    raise HTTPException(
                        status_code=400,
                        detail="verifier_result.note must not contain control characters",
                    )
                verifier_result["note"] = note[:240]
        with session_scope(db_url) as session:
            existing_proof = _existing_payout_proof_for_submission(
                session, bounty_id, clean_submission_url
            )
            if existing_proof is not None:
                return JSONResponse(
                    status_code=409,
                    content=_payout_response_from_proof(existing_proof, status="already_paid"),
                )
            try:
                proposal = propose_treasury_action(
                    session,
                    action="pay_bounty",
                    payload={
                        "bounty_id": bounty_id,
                        "to_account": requested_account,
                        "submission_url": clean_submission_url,
                        "accepted_by": accepted_by,
                        "note": verifier_result.get("note"),
                    },
                    proposed_by=admin_login,
                )
            except LedgerError as exc:
                raise _ledger_http_error(exc) from exc
            return proposal_to_dict(proposal)

    @app.post("/api/v1/bounties/{bounty_id}/close")
    async def api_close_bounty(
        bounty_id: int,
        request: Request,
        admin_login: str = Depends(require_admin_token),
    ) -> dict[str, Any]:
        bounty_id = positive_bounty_id(bounty_id)
        data = await json_object(request)
        reference = optional_str(data, "reference") if data.get("reference") is not None else None
        closed_by = optional_str(data, "closed_by", admin_login)
        with session_scope(db_url) as session:
            try:
                proposal = propose_treasury_action(
                    session,
                    action="close_bounty",
                    payload={
                        "bounty_id": bounty_id,
                        "closed_by": closed_by,
                        "reference": reference,
                    },
                    proposed_by=admin_login,
                )
            except LedgerError as exc:
                raise _ledger_http_error(exc) from exc
            return proposal_to_dict(proposal)

    return {
        "list_bounties_by_status": _list_bounties_by_status,
        "get_bounty_detail": api_bounty,
    }
