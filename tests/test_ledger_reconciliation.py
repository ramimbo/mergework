from __future__ import annotations

from sqlalchemy import select

from app.db import create_schema, session_scope
from app.ledger.reconciliation import (
    duplicate_accepted_source_urls,
    duplicate_source_summary,
    payout_reconciliation_summary,
    reconcile_accepted_payouts,
)
from app.ledger.service import (
    add_ledger_entry,
    canonical_json,
    create_bounty,
    ensure_genesis,
    pay_bounty,
    reserve_account_for_bounty,
    verify_hash_chain,
    verify_supply_conservation,
)
from app.models import LedgerEntry, Proof, Submission


def test_reconcile_accepted_payouts_reports_already_paid_submission(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=35,
            issue_url="https://github.com/ramimbo/mergework/issues/35",
            title="Reconcile paid submissions",
            reward_mrwk="12",
            acceptance="Maintainer applies mrwk:accepted.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/35",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        checks = reconcile_accepted_payouts(session)
        summary = payout_reconciliation_summary(checks)

        assert summary == {
            "accepted_submissions": 1,
            "paid": 1,
            "missing_payment": 0,
            "duplicate_payment_evidence": 0,
            "mismatched_payment_evidence": 0,
        }
        assert checks[0].status == "paid"
        assert checks[0].submission_url == "https://github.com/ramimbo/mergework/pull/35"
        assert checks[0].evidence[0].proof_hash == proof.hash
        assert checks[0].evidence[0].matches_submission is True


def test_reconcile_accepted_payouts_reports_missing_payment(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=36,
            issue_url="https://github.com/ramimbo/mergework/issues/36",
            title="Reconcile missing payments",
            reward_mrwk="12",
            acceptance="Maintainer applies mrwk:accepted.",
        )
        session.add(
            Submission(
                bounty_id=bounty.id,
                submitter_account="github:bob",
                url="https://github.com/ramimbo/mergework/pull/36",
                status="accepted",
                verifier_result=canonical_json({"label": "mrwk:accepted"}),
            )
        )
        session.flush()

        checks = reconcile_accepted_payouts(session)
        summary = payout_reconciliation_summary(checks)

        assert summary["accepted_submissions"] == 1
        assert summary["missing_payment"] == 1
        assert checks[0].status == "missing_payment"
        assert checks[0].evidence == ()


def test_reconcile_accepted_payouts_matches_legacy_proof_source_url(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=38,
            issue_url="https://github.com/ramimbo/mergework/issues/38",
            title="Reconcile legacy source proofs",
            reward_mrwk="12",
            acceptance="Maintainer applies mrwk:accepted.",
        )
        submission = Submission(
            bounty_id=bounty.id,
            submitter_account="github:dora",
            url="https://github.com/ramimbo/mergework/pull/38",
            status="accepted",
            verifier_result=canonical_json({"label": "mrwk:accepted"}),
        )
        session.add(submission)
        session.flush()
        entry = add_ledger_entry(
            session,
            entry_type="bounty_payment",
            from_account=reserve_account_for_bounty(bounty.id),
            to_account="github:dora",
            amount_microunits=bounty.reward_microunits,
            reference=submission.url,
        )
        session.add(
            Proof(
                hash="a" * 64,
                ledger_sequence=entry.sequence,
                bounty_id=bounty.id,
                submission_id=None,
                kind="bounty_payment",
                public_json=canonical_json(
                    {
                        "kind": "bounty_payment",
                        "bounty_id": bounty.id,
                        "submission_url": submission.url,
                    }
                ),
            )
        )
        session.flush()

        checks = reconcile_accepted_payouts(session)
        summary = payout_reconciliation_summary(checks)

        assert summary["paid"] == 1
        assert summary["missing_payment"] == 0
        assert checks[0].status == "paid"
        assert checks[0].evidence[0].proof_hash == "a" * 64
        assert checks[0].evidence[0].matches_submission is True


def test_reconcile_accepted_payouts_reports_legacy_source_mismatch(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=39,
            issue_url="https://github.com/ramimbo/mergework/issues/39",
            title="Reconcile mismatched legacy proof",
            reward_mrwk="12",
            acceptance="Maintainer applies mrwk:accepted.",
        )
        submission = Submission(
            bounty_id=bounty.id,
            submitter_account="github:erin",
            url="https://github.com/ramimbo/mergework/pull/39",
            status="accepted",
            verifier_result=canonical_json({"label": "mrwk:accepted"}),
        )
        session.add(submission)
        session.flush()
        entry = add_ledger_entry(
            session,
            entry_type="bounty_payment",
            from_account=reserve_account_for_bounty(bounty.id),
            to_account="github:mallory",
            amount_microunits=bounty.reward_microunits,
            reference=submission.url,
        )
        session.add(
            Proof(
                hash="b" * 64,
                ledger_sequence=entry.sequence,
                bounty_id=bounty.id,
                submission_id=None,
                kind="bounty_payment",
                public_json=canonical_json(
                    {
                        "kind": "bounty_payment",
                        "bounty_id": bounty.id,
                        "submission_url": submission.url,
                    }
                ),
            )
        )
        session.flush()

        checks = reconcile_accepted_payouts(session)
        summary = payout_reconciliation_summary(checks)

        assert summary["mismatched_payment_evidence"] == 1
        assert checks[0].status == "mismatched_payment_evidence"
        assert checks[0].evidence[0].proof_hash == "b" * 64
        assert checks[0].evidence[0].matches_submission is False


def test_reconcile_accepted_payouts_rejects_wrong_explicit_submission_link(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=40,
            issue_url="https://github.com/ramimbo/mergework/issues/40",
            title="Reconcile explicit proof links",
            reward_mrwk="12",
            max_awards=2,
            acceptance="Maintainer applies mrwk:accepted.",
        )
        first_submission = Submission(
            bounty_id=bounty.id,
            submitter_account="github:fran",
            url="https://github.com/ramimbo/mergework/pull/40",
            status="accepted",
            verifier_result=canonical_json({"label": "mrwk:accepted"}),
        )
        second_submission = Submission(
            bounty_id=bounty.id,
            submitter_account="github:gabe",
            url="https://github.com/ramimbo/mergework/pull/41",
            status="accepted",
            verifier_result=canonical_json({"label": "mrwk:accepted"}),
        )
        session.add_all([first_submission, second_submission])
        session.flush()
        entry = add_ledger_entry(
            session,
            entry_type="bounty_payment",
            from_account=reserve_account_for_bounty(bounty.id),
            to_account=first_submission.submitter_account,
            amount_microunits=bounty.reward_microunits,
            reference=first_submission.url,
        )
        session.add(
            Proof(
                hash="c" * 64,
                ledger_sequence=entry.sequence,
                bounty_id=bounty.id,
                submission_id=second_submission.id,
                kind="bounty_payment",
                public_json=canonical_json(
                    {
                        "kind": "bounty_payment",
                        "bounty_id": bounty.id,
                        "submission_url": first_submission.url,
                    }
                ),
            )
        )
        session.flush()

        checks = reconcile_accepted_payouts(session)
        checks_by_url = {check.submission_url: check for check in checks}

        first_check = checks_by_url[first_submission.url]
        assert first_check.status == "missing_payment"
        assert first_check.evidence == ()
        second_check = checks_by_url[second_submission.url]
        assert second_check.status == "mismatched_payment_evidence"
        assert second_check.evidence[0].proof_hash == "c" * 64
        assert second_check.evidence[0].matches_submission is False


def test_reconcile_accepted_payouts_reports_duplicate_payment_evidence(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=37,
            issue_url="https://github.com/ramimbo/mergework/issues/37",
            title="Reconcile duplicate payments",
            reward_mrwk="12",
            acceptance="Maintainer applies mrwk:accepted.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:carol",
            submission_url="https://github.com/ramimbo/mergework/pull/37",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        session.add(
            Proof(
                hash="f" * 64,
                ledger_sequence=proof.ledger_sequence,
                bounty_id=bounty.id,
                submission_id=proof.submission_id,
                kind="bounty_payment",
                public_json=proof.public_json,
            )
        )
        session.flush()

        checks = reconcile_accepted_payouts(session)
        summary = payout_reconciliation_summary(checks)

        assert summary["duplicate_payment_evidence"] == 1
        assert checks[0].status == "duplicate_payment_evidence"
        assert len(checks[0].evidence) == 2


def test_reconcile_accepted_payouts_reports_mismatched_payment_evidence(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=38,
            issue_url="https://github.com/ramimbo/mergework/issues/38",
            title="Reconcile mismatched payments",
            reward_mrwk="12",
            acceptance="Maintainer applies mrwk:accepted.",
        )
        submission = Submission(
            bounty_id=bounty.id,
            submitter_account="github:dana",
            url="https://github.com/ramimbo/mergework/pull/38",
            status="accepted",
            verifier_result=canonical_json({"label": "mrwk:accepted"}),
        )
        session.add(submission)
        session.flush()
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:erin",
            submission_url="https://github.com/ramimbo/mergework/pull/39",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        session.add(
            Proof(
                hash="e" * 64,
                ledger_sequence=proof.ledger_sequence,
                bounty_id=bounty.id,
                submission_id=submission.id,
                kind="bounty_payment",
                public_json=proof.public_json,
            )
        )
        session.flush()

        checks = reconcile_accepted_payouts(session)
        mismatched = [check for check in checks if check.submission_id == submission.id]

        assert len(mismatched) == 1
        assert mismatched[0].status == "mismatched_payment_evidence"
        assert mismatched[0].evidence[0].matches_submission is False
        assert verify_hash_chain(session) is True
        assert verify_supply_conservation(session) is True


def test_duplicate_accepted_source_urls_groups_distinct_accepted_submissions(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        first_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=39,
            issue_url="https://github.com/ramimbo/mergework/issues/39",
            title="First source URL bounty",
            reward_mrwk="5",
            acceptance="Maintainer applies mrwk:accepted.",
        )
        second_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=40,
            issue_url="https://github.com/ramimbo/mergework/issues/40",
            title="Second source URL bounty",
            reward_mrwk="5",
            acceptance="Maintainer applies mrwk:accepted.",
        )
        third_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=41,
            issue_url="https://github.com/ramimbo/mergework/issues/41",
            title="Third source URL bounty",
            reward_mrwk="5",
            acceptance="Maintainer applies mrwk:accepted.",
        )
        source_url = "https://github.com/ramimbo/mergework/pull/281"
        session.add_all(
            [
                Submission(
                    bounty_id=first_bounty.id,
                    submitter_account="github:alice",
                    # The bad port is deliberate; this also exercises case,
                    # path, query, and fragment canonicalization in one URL.
                    url="https://github.com:bad/Ramimbo/MergeWork/PULL/281/files/"
                    "?diff=split#discussion_r1",
                    status="accepted",
                    verifier_result=canonical_json({"label": "mrwk:accepted"}),
                ),
                Submission(
                    bounty_id=second_bounty.id,
                    submitter_account="github:bob",
                    url="https://github.com/ramimbo/mergework/pull/281/#discussion_r1",
                    status="accepted",
                    verifier_result=canonical_json({"label": "mrwk:accepted"}),
                ),
                Submission(
                    bounty_id=third_bounty.id,
                    submitter_account="github:carol",
                    url="http://github.com/Ramimbo/MergeWork/pull/281/commits?plain=1",
                    status="accepted",
                    verifier_result=canonical_json({"label": "mrwk:accepted"}),
                ),
            ]
        )
        session.flush()
        ledger_entries_before = len(session.scalars(select(LedgerEntry)).all())
        proofs_before = len(session.scalars(select(Proof)).all())

        groups = duplicate_accepted_source_urls(session)

        assert len(groups) == 1
        assert groups[0].source_url == source_url
        assert {ref.bounty_issue for ref in groups[0].submissions} == {
            "ramimbo/mergework#39",
            "ramimbo/mergework#40",
            "ramimbo/mergework#41",
        }
        assert duplicate_source_summary(groups) == {
            "duplicate_source_urls": 1,
            "duplicate_source_submissions": 3,
        }
        assert len(session.scalars(select(LedgerEntry)).all()) == ledger_entries_before
        assert len(session.scalars(select(Proof)).all()) == proofs_before
        assert verify_hash_chain(session) is True
        assert verify_supply_conservation(session) is True
