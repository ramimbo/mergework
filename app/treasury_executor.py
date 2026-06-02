from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.github_issue_finalization import finalize_created_bounty_issue, finalize_paid_bounty_issue
from app.ledger.service import LedgerError
from app.models import Bounty, TreasuryProposal, utc_now
from app.treasury import (
    execute_treasury_proposal,
    proposal_to_dict,
    record_proposal_result_field,
)

Finalizer = Callable[..., dict[str, Any]]
PaidIssueFinalizer = Callable[..., dict[str, Any]]
PAID_ISSUE_FINALIZED_STATUSES = {"updated", "closed", "already_finalized"}


def _db_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def due_treasury_proposal_ids(db_url: str, *, limit: int = 25) -> list[int]:
    now = _db_utc(utc_now())
    with session_scope(db_url) as session:
        return [
            int(proposal_id)
            for proposal_id in session.scalars(
                select(TreasuryProposal.id)
                .where(
                    TreasuryProposal.status == "pending",
                    TreasuryProposal.executes_after <= now,
                )
                .order_by(TreasuryProposal.executes_after.asc(), TreasuryProposal.id.asc())
                .limit(limit)
            ).all()
        ]


def paid_bounty_ids_needing_github_finalization(db_url: str, *, limit: int = 25) -> list[int]:
    with session_scope(db_url) as session:
        return [
            int(bounty_id)
            for bounty_id in session.scalars(
                select(Bounty.id)
                .where(
                    Bounty.status == "paid",
                    Bounty.github_paid_issue_finalized_at.is_(None),
                )
                .order_by(Bounty.id.asc())
                .limit(limit)
            ).all()
        ]


def _paid_bounty_finalization_payload(bounty: Bounty) -> dict[str, object]:
    return {
        "id": int(bounty.id),
        "repo": bounty.repo,
        "issue_number": int(bounty.issue_number),
        "issue_url": bounty.issue_url,
        "title": bounty.title,
    }


def execute_treasury_proposal_with_finalization(
    db_url: str,
    *,
    proposal_id: int,
    executed_by: str,
    github_issue_token: str,
    public_base_url: str,
    finalizer: Finalizer | None = None,
) -> dict[str, Any]:
    with session_scope(db_url) as session:
        proposal = execute_treasury_proposal(
            session, proposal_id=proposal_id, executed_by=executed_by
        )
        response = proposal_to_dict(proposal)
    if response["action"] != "create_bounty":
        return response
    bounty = response.get("result", {}).get("bounty")
    if not isinstance(bounty, dict):
        return response
    issue_finalizer = finalizer or finalize_created_bounty_issue
    try:
        finalization = issue_finalizer(
            github_token=github_issue_token,
            public_base_url=public_base_url,
            bounty=bounty,
        )
    except Exception as exc:
        finalization = {
            "status": "failed",
            "reason": f"github issue finalization failed: {type(exc).__name__}",
        }
    with session_scope(db_url) as session:
        proposal = record_proposal_result_field(
            session,
            proposal_id=proposal_id,
            field="github_issue_finalization",
            value=finalization,
        )
        return proposal_to_dict(proposal)


def finalize_paid_bounty_issues(
    db_url: str,
    *,
    github_issue_token: str,
    public_base_url: str,
    limit: int = 25,
    finalizer: PaidIssueFinalizer | None = None,
) -> dict[str, Any]:
    bounty_ids = paid_bounty_ids_needing_github_finalization(db_url, limit=limit)
    issue_finalizer = finalizer or finalize_paid_bounty_issue
    results: list[dict[str, Any]] = []
    updated = 0
    failed = 0
    skipped = 0
    for bounty_id in bounty_ids:
        with session_scope(db_url) as session:
            bounty = session.get(Bounty, bounty_id)
            if (
                bounty is None
                or bounty.status != "paid"
                or bounty.github_paid_issue_finalized_at is not None
            ):
                continue
            bounty_payload = _paid_bounty_finalization_payload(bounty)
        try:
            finalization = issue_finalizer(
                github_token=github_issue_token,
                public_base_url=public_base_url,
                bounty=bounty_payload,
            )
        except Exception as exc:
            finalization = {
                "status": "failed",
                "reason": f"github paid issue finalization failed: {type(exc).__name__}",
            }
        status = str(finalization.get("status") or "")
        if status in PAID_ISSUE_FINALIZED_STATUSES:
            with session_scope(db_url) as session:
                bounty = session.get(Bounty, bounty_id)
                if (
                    bounty is not None
                    and bounty.status == "paid"
                    and bounty.github_paid_issue_finalized_at is None
                ):
                    bounty.github_paid_issue_finalized_at = utc_now()
                    bounty.github_paid_issue_finalization = json.dumps(
                        finalization, sort_keys=True, separators=(",", ":")
                    )
            updated += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1
        results.append(
            {
                "bounty_id": int(bounty_id),
                "status": status or "unknown",
                "result": finalization,
            }
        )
    return {
        "type": "paid_bounty_issue_finalization",
        "attempted": len(bounty_ids),
        "updated": updated,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }


def execute_due_treasury_proposals(
    db_url: str,
    *,
    github_issue_token: str,
    public_base_url: str,
    executed_by: str = "treasury-executor",
    limit: int = 25,
    finalizer: Finalizer | None = None,
    paid_issue_finalizer: PaidIssueFinalizer | None = None,
) -> dict[str, Any]:
    proposal_ids = due_treasury_proposal_ids(db_url, limit=limit)
    results: list[dict[str, Any]] = []
    executed = 0
    failed = 0
    for proposal_id in proposal_ids:
        action = "unknown"
        with session_scope(db_url) as session:
            proposal = session.get(TreasuryProposal, proposal_id)
            if proposal is not None:
                action = proposal.action
        try:
            response = execute_treasury_proposal_with_finalization(
                db_url,
                proposal_id=proposal_id,
                executed_by=executed_by,
                github_issue_token=github_issue_token,
                public_base_url=public_base_url,
                finalizer=finalizer,
            )
        except LedgerError as exc:
            failed += 1
            results.append(
                {
                    "proposal_id": proposal_id,
                    "action": action,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            continue
        executed += 1
        item = {
            "proposal_id": int(response["id"]),
            "action": str(response["action"]),
            "status": str(response["status"]),
            "result": response["result"],
        }
        finalization = response.get("result", {}).get("github_issue_finalization")
        if finalization is not None:
            item["github_issue_finalization"] = finalization
        results.append(item)
    paid_issue_report = finalize_paid_bounty_issues(
        db_url,
        github_issue_token=github_issue_token,
        public_base_url=public_base_url,
        limit=limit,
        finalizer=paid_issue_finalizer,
    )
    return {
        "type": "treasury_executor_run",
        "attempted": len(proposal_ids),
        "executed": executed,
        "failed": failed,
        "results": results,
        "paid_issue_finalization": paid_issue_report,
    }
