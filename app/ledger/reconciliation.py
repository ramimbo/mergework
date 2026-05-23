from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger.service import reserve_account_for_bounty
from app.models import Bounty, LedgerEntry, Proof, Submission


@dataclass(frozen=True)
class PayoutReconciliationFinding:
    code: str
    bounty_id: int
    submission_id: int
    submission_url: str
    detail: str

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def reconcile_accepted_submission_payouts(
    session: Session,
) -> list[PayoutReconciliationFinding]:
    """Report accepted submissions that lack exactly one matching payment proof."""
    submissions = session.scalars(
        select(Submission).where(Submission.status == "accepted").order_by(Submission.id)
    ).all()
    findings: list[PayoutReconciliationFinding] = []

    for submission in submissions:
        bounty = session.get(Bounty, submission.bounty_id)
        proofs = session.scalars(
            select(Proof).where(Proof.submission_id == submission.id).order_by(Proof.hash)
        ).all()
        payments = session.scalars(
            select(LedgerEntry)
            .where(
                LedgerEntry.entry_type == "bounty_payment",
                LedgerEntry.reference == submission.url,
            )
            .order_by(LedgerEntry.sequence)
        ).all()

        if not proofs:
            findings.append(
                _finding(
                    "missing_proof",
                    submission,
                    "accepted submission has no public proof row",
                )
            )
        elif len(proofs) > 1:
            findings.append(
                _finding(
                    "duplicate_proof",
                    submission,
                    f"accepted submission has {len(proofs)} public proof rows",
                )
            )

        if not payments:
            findings.append(
                _finding(
                    "missing_payment",
                    submission,
                    "accepted submission has no bounty payment ledger entry",
                )
            )
        elif len(payments) > 1:
            findings.append(
                _finding(
                    "duplicate_payment",
                    submission,
                    f"accepted submission has {len(payments)} matching payment ledger entries",
                )
            )

        if bounty is None:
            findings.append(
                _finding(
                    "missing_bounty",
                    submission,
                    "accepted submission references a missing bounty",
                )
            )
            continue

        reserve_account = reserve_account_for_bounty(bounty.id)
        for payment in payments:
            if payment.from_account != reserve_account:
                findings.append(
                    _finding(
                        "payment_source_mismatch",
                        submission,
                        f"payment entry {payment.sequence} is from {payment.from_account}",
                    )
                )
            if payment.amount_microunits != bounty.reward_microunits:
                findings.append(
                    _finding(
                        "payment_amount_mismatch",
                        submission,
                        f"payment entry {payment.sequence} amount does not match bounty reward",
                    )
                )

        for proof in proofs:
            findings.extend(_proof_findings(session, submission, bounty, proof))

    return findings


def reconciliation_findings_to_dicts(
    findings: list[PayoutReconciliationFinding],
) -> list[dict[str, str | int]]:
    return [finding.to_dict() for finding in findings]


def _finding(
    code: str,
    submission: Submission,
    detail: str,
) -> PayoutReconciliationFinding:
    return PayoutReconciliationFinding(
        code=code,
        bounty_id=submission.bounty_id,
        submission_id=submission.id,
        submission_url=submission.url,
        detail=detail,
    )


def _proof_findings(
    session: Session,
    submission: Submission,
    bounty: Bounty,
    proof: Proof,
) -> list[PayoutReconciliationFinding]:
    findings: list[PayoutReconciliationFinding] = []
    ledger_entry = session.get(LedgerEntry, proof.ledger_sequence)
    if ledger_entry is None:
        return [
            _finding(
                "proof_ledger_missing",
                submission,
                f"proof {proof.hash} references missing ledger entry {proof.ledger_sequence}",
            )
        ]

    if ledger_entry.reference != submission.url:
        findings.append(
            _finding(
                "proof_ledger_reference_mismatch",
                submission,
                (
                    f"proof {proof.hash} references ledger entry {ledger_entry.sequence} "
                    "with a different reference"
                ),
            )
        )

    try:
        public_payload: Any = json.loads(proof.public_json)
    except json.JSONDecodeError:
        findings.append(
            _finding("invalid_proof_payload", submission, f"proof {proof.hash} is not JSON")
        )
        return findings
    if not isinstance(public_payload, dict):
        findings.append(
            _finding(
                "invalid_proof_payload", submission, f"proof {proof.hash} payload is not an object"
            )
        )
        return findings

    if public_payload.get("bounty_id") != bounty.id:
        findings.append(
            _finding(
                "proof_bounty_mismatch",
                submission,
                f"proof {proof.hash} does not point at bounty {bounty.id}",
            )
        )
    if public_payload.get("submission_url") != submission.url:
        findings.append(
            _finding(
                "proof_submission_mismatch",
                submission,
                f"proof {proof.hash} does not point at the accepted submission URL",
            )
        )
    if public_payload.get("ledger_sequence") != ledger_entry.sequence:
        findings.append(
            _finding(
                "proof_sequence_mismatch",
                submission,
                f"proof {proof.hash} sequence does not match its ledger entry",
            )
        )
    if public_payload.get("ledger_hash") != ledger_entry.entry_hash:
        findings.append(
            _finding(
                "proof_hash_mismatch",
                submission,
                f"proof {proof.hash} ledger hash does not match entry {ledger_entry.sequence}",
            )
        )

    return findings
