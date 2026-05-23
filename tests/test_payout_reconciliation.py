from __future__ import annotations

from app.db import create_schema, session_scope
from app.ledger.service import (
    add_ledger_entry,
    create_bounty,
    ensure_genesis,
    pay_bounty,
    reconcile_accepted_work_payouts,
)
from app.models import Submission


def test_reconciliation_reports_missing_payment_evidence(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=35,
            issue_url="https://github.com/ramimbo/mergework/issues/35",
            title="Accepted work without payout proof",
            reward_mrwk="25",
            acceptance="Maintainer applies mrwk:accepted.",
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
        session.flush()

        issues = reconcile_accepted_work_payouts(session)

    assert issues == [
        {
            "submission_id": "1",
            "bounty_id": "1",
            "submitter_account": "github:alice",
            "submission_url": "https://github.com/ramimbo/mergework/pull/35",
            "problem": "missing_payment_evidence",
            "detail": (
                "proofs=0 ledger_payments=0; expected one proof and one matching "
                "bounty_payment ledger entry"
            ),
        }
    ]


def test_reconciliation_accepts_already_paid_submission(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=36,
            issue_url="https://github.com/ramimbo/mergework/issues/36",
            title="Accepted work with payout proof",
            reward_mrwk="25",
            acceptance="Maintainer applies mrwk:accepted.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/36",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        issues = reconcile_accepted_work_payouts(session)

    assert issues == []


def test_reconciliation_reports_duplicate_payment_evidence(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=37,
            issue_url="https://github.com/ramimbo/mergework/issues/37",
            title="Duplicate payment evidence",
            reward_mrwk="25",
            acceptance="Maintainer applies mrwk:accepted.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/37",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        add_ledger_entry(
            session,
            entry_type="bounty_payment",
            from_account="reserve:bounty:1",
            to_account="github:alice",
            amount_microunits=1,
            reference="https://github.com/ramimbo/mergework/pull/37",
        )

        issues = reconcile_accepted_work_payouts(session)

    assert issues[0]["problem"] == "duplicate_payment_evidence"
    assert issues[0]["detail"].startswith("proofs=1 ledger_payments=2")
