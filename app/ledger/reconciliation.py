from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ledger.service import format_mrwk
from app.models import Bounty, LedgerEntry, Proof, Submission

GITHUB_SOURCE_PATH_RE = re.compile(
    r"/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<kind>issues|pull)/(?P<number>\d+)"
    r"(?P<view>/(?:files|commits|checks))?/?",
    re.IGNORECASE,
)

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


@dataclass(frozen=True)
class AcceptedSourceReference:
    bounty_id: int
    bounty_issue: str
    submission_id: int
    submitter_account: str
    submission_url: str


@dataclass(frozen=True)
class DuplicateAcceptedSourceUrl:
    source_url: str
    submissions: tuple[AcceptedSourceReference, ...]


def reconcile_accepted_payouts(session: Session) -> list[AcceptedPayoutCheck]:
    rows = session.execute(
        select(Submission, Bounty)
        .join(Bounty, Bounty.id == Submission.bounty_id)
        .where(Submission.status == "accepted")
        .order_by(Submission.id)
    ).all()
    checks: list[AcceptedPayoutCheck] = []
    for submission, bounty in rows:
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


def duplicate_accepted_source_urls(session: Session) -> list[DuplicateAcceptedSourceUrl]:
    rows = session.execute(
        select(Submission, Bounty)
        .join(Bounty, Bounty.id == Submission.bounty_id)
        .where(Submission.status == "accepted")
        .order_by(Submission.id)
    ).all()
    groups: dict[str, list[AcceptedSourceReference]] = {}
    for submission, bounty in rows:
        source_url = _canonical_source_url(submission.url)
        groups.setdefault(source_url, []).append(
            AcceptedSourceReference(
                bounty_id=bounty.id,
                bounty_issue=f"{bounty.repo}#{bounty.issue_number}",
                submission_id=submission.id,
                submitter_account=submission.submitter_account,
                submission_url=submission.url,
            )
        )
    return [
        DuplicateAcceptedSourceUrl(source_url=source_url, submissions=tuple(submissions))
        for source_url, submissions in groups.items()
        if len(submissions) > 1
    ]


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


def duplicate_source_summary(groups: Sequence[DuplicateAcceptedSourceUrl]) -> dict[str, int]:
    return {
        "duplicate_source_urls": len(groups),
        "duplicate_source_submissions": sum(len(group.submissions) for group in groups),
    }


def _canonical_source_url(url: str) -> str:
    clean = url.strip()
    parsed = urlsplit(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return clean
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    if (parsed.scheme.lower(), port) in {("http", 80), ("https", 443)}:
        port = None
    netloc = f"{host}:{port}" if port is not None else host
    path = parsed.path.rstrip("/") or parsed.path
    query = parsed.query
    fragment = parsed.fragment
    if host == "github.com":
        match = GITHUB_SOURCE_PATH_RE.fullmatch(path)
        if match:
            path = (
                f"/{match['owner'].lower()}/{match['repo'].lower()}/"
                f"{match['kind'].lower()}/{match['number']}"
            )
            query = ""
            fragment = ""
            return urlunsplit(("https", netloc, path, query, fragment))
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, fragment))


def _payout_evidence(
    session: Session, submission: Submission, bounty: Bounty
) -> tuple[PayoutEvidence, ...]:
    proofs = session.scalars(
        select(Proof)
        .where(
            Proof.kind == "bounty_payment",
            or_(Proof.submission_id == submission.id, Proof.bounty_id == bounty.id),
        )
        .order_by(Proof.created_at, Proof.hash)
    ).all()
    evidence: list[PayoutEvidence] = []
    for proof in proofs:
        if not _matches_submission_source(proof, submission, bounty):
            continue
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


def _matches_submission_source(proof: Proof, submission: Submission, bounty: Bounty) -> bool:
    if proof.submission_id == submission.id:
        return True
    if proof.submission_id is not None:
        return False
    if proof.bounty_id != bounty.id:
        return False
    try:
        data = json.loads(proof.public_json)
    except ValueError:
        return False
    return (
        isinstance(data, dict)
        and data.get("kind") == "bounty_payment"
        and data.get("submission_url") == submission.url
    )


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
