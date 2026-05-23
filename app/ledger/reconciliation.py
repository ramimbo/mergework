from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger.service import format_mrwk
from app.models import Bounty, LedgerEntry, Proof, Submission

PayoutReconciliationStatus = Literal[
    "paid",
    "missing_payment",
    "duplicate_payment_evidence",
    "mismatched_payment_evidence",
]


@dataclass(frozen=True)
class PayoutEvidence:
    proof_hash: str
    ledger_sequence: int
    ledger_type: str | None
    reference: str | None
    to_account: str | None
    amount_mrwk: str | None
    matches_submission: bool


@dataclass(frozen=True)
class AcceptedPayoutCheck:
    status: PayoutReconciliationStatus
    bounty_id: int
    bounty_issue: str
    submission_id: int
    submitter_account: str
    submission_url: str
    evidence: tuple[PayoutEvidence, ...]


def reconcile_accepted_payouts(session: Session) -> list[AcceptedPayoutCheck]:
    submissions = session.scalars(
        select(Submission).where(Submission.status == "accepted").order_by(Submission.id)
    ).all()
    checks: list[AcceptedPayoutCheck] = []
    for submission in submissions:
        bounty = session.get(Bounty, submission.bounty_id)
        if bounty is None:
            continue
        evidence = _payout_evidence(session, submission, bounty)
        checks.append(
            AcceptedPayoutCheck(
                status=_reconciliation_status(evidence),
                bounty_id=bounty.id,
                bounty_issue=f"{bounty.repo}#{bounty.issue_number}",
                submission_id=submission.id,
                submitter_account=submission.submitter_account,
                submission_url=submission.url,
                evidence=evidence,
            )
        )
    return checks


def payout_reconciliation_summary(checks: Sequence[AcceptedPayoutCheck]) -> dict[str, int]:
    summary = {
        "accepted_submissions": len(checks),
        "paid": 0,
        "missing_payment": 0,
        "duplicate_payment_evidence": 0,
        "mismatched_payment_evidence": 0,
    }
    for check in checks:
        summary[check.status] += 1
    return summary


def _payout_evidence(
    session: Session, submission: Submission, bounty: Bounty
) -> tuple[PayoutEvidence, ...]:
    proofs = session.scalars(
        select(Proof)
        .where(Proof.submission_id == submission.id, Proof.kind == "bounty_payment")
        .order_by(Proof.created_at, Proof.hash)
    ).all()
    evidence: list[PayoutEvidence] = []
    for proof in proofs:
        entry = session.get(LedgerEntry, proof.ledger_sequence)
        evidence.append(
            PayoutEvidence(
                proof_hash=proof.hash,
                ledger_sequence=proof.ledger_sequence,
                ledger_type=entry.entry_type if entry else None,
                reference=entry.reference if entry else None,
                to_account=entry.to_account if entry else None,
                amount_mrwk=format_mrwk(entry.amount_microunits) if entry else None,
                matches_submission=_matches_submission(entry, proof, submission, bounty),
            )
        )
    return tuple(evidence)


def _matches_submission(
    entry: LedgerEntry | None, proof: Proof, submission: Submission, bounty: Bounty
) -> bool:
    return (
        proof.bounty_id == bounty.id
        and entry is not None
        and entry.entry_type == "bounty_payment"
        and entry.reference == submission.url
        and entry.to_account == submission.submitter_account
        and entry.amount_microunits == bounty.reward_microunits
    )


def _reconciliation_status(
    evidence: Sequence[PayoutEvidence],
) -> PayoutReconciliationStatus:
    if not evidence:
        return "missing_payment"
    if len(evidence) > 1:
        return "duplicate_payment_evidence"
    if not evidence[0].matches_submission:
        return "mismatched_payment_evidence"
    return "paid"
