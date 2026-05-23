from __future__ import annotations

from sqlalchemy import select

from app.db import create_schema, session_scope
from app.ledger.reconciliation import reconcile_accepted_submission_payouts
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty
from app.models import LedgerEntry, Proof, Submission


def test_reconciliation_reports_missing_payment_evidence(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=35,
            issue_url="https://github.com/ramimbo/mergework/issues/35",
            title="Reconcile accepted work",
            reward_mrwk="250",
            acceptance="Maintainer applies mrwk:accepted",
        )
        session.add(
            Submission(
                bounty_id=bounty.id,
                submitter_account="github:alice",
                url="https://github.com/ramimbo/mergework/pull/35",
                status="accepted",
                verifier_result='{"label":"mrwk:accepted"}',
            )
        )
        before_height = session.scalar(
            select(LedgerEntry.sequence).order_by(LedgerEntry.sequence.desc())
        )

        findings = reconcile_accepted_submission_payouts(session)
        after_height = session.scalar(
            select(LedgerEntry.sequence).order_by(LedgerEntry.sequence.desc())
        )

    assert before_height == after_height
    assert {finding.code for finding in findings} == {"missing_proof", "missing_payment"}
    assert findings[0].submission_url == "https://github.com/ramimbo/mergework/pull/35"


def test_reconciliation_accepts_already_paid_submission(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=36,
            issue_url="https://github.com/ramimbo/mergework/issues/36",
            title="Paid accepted work",
            reward_mrwk="100",
            acceptance="Maintainer applies mrwk:accepted",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/36",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        findings = reconcile_accepted_submission_payouts(session)

    assert findings == []


def test_reconciliation_reports_duplicate_payment_evidence(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=37,
            issue_url="https://github.com/ramimbo/mergework/issues/37",
            title="Duplicate proof guard",
            reward_mrwk="150",
            acceptance="Maintainer applies mrwk:accepted",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/37",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        session.add(
            Proof(
                hash="1" * 64,
                ledger_sequence=proof.ledger_sequence,
                bounty_id=proof.bounty_id,
                submission_id=proof.submission_id,
                kind=proof.kind,
                public_json=proof.public_json,
            )
        )

        findings = reconcile_accepted_submission_payouts(session)

    assert [finding.code for finding in findings] == ["duplicate_proof"]
