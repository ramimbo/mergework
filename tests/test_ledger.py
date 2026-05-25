from __future__ import annotations

import pytest

from app.db import create_schema, make_engine, session_scope
from app.ledger.reconciliation import (
    exhausted_round_overflow_detection,
    overflow_summary,
    payout_reconciliation_summary,
    reconcile_accepted_payouts,
)
from app.ledger.service import (
    GENESIS_SUPPLY_MICRO,
    TREASURY_ACCOUNT,
    LedgerError,
    canonical_json,
    close_bounty,
    create_bounty,
    ensure_genesis,
    get_balance,
    pay_bounty,
    register_wallet,
    reserve_account_for_bounty,
    resolve_payout_account,
    verify_hash_chain,
    verify_supply_conservation,
)
from app.models import Bounty, LedgerEntry, Proof, Submission


def test_genesis_creates_fixed_supply_once(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        first = ensure_genesis(session)
        second = ensure_genesis(session)

        assert first.sequence == 1
        assert second.sequence == 1
        assert get_balance(session, TREASURY_ACCOUNT) == GENESIS_SUPPLY_MICRO
        assert verify_hash_chain(session) is True
        assert verify_supply_conservation(session) is True


def test_make_engine_accepts_windows_absolute_sqlite_url(tmp_path) -> None:
    database_path = tmp_path / "nested" / "mergework.sqlite3"
    engine = make_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SELECT 1")
    finally:
        engine.dispose()

    assert database_path.parent.exists()


def test_bounty_reserve_and_payout_conserve_supply(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=7,
            issue_url="https://github.com/ramimbo/mergework/issues/7",
            title="Write ledger tests",
            reward_mrwk="125.5",
            acceptance="Merged PR with tests",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/8",
            accepted_by="maintainer",
            verifier_result={"merged": True, "ci": "passed"},
        )

        assert get_balance(session, "github:alice") == 125_500_000
        assert get_balance(session, TREASURY_ACCOUNT) == GENESIS_SUPPLY_MICRO - 125_500_000
        assert proof.hash
        assert verify_hash_chain(session) is True
        assert verify_supply_conservation(session) is True


def test_resolve_payout_account_accepts_mixed_case_prefixes(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        wallet = register_wallet(session, public_key_hex="1" * 64)
        mixed_wallet = "MRWK1" + wallet.address.removeprefix("mrwk1").upper()

        assert resolve_payout_account(session, " GitHub:Alice ") == "github:alice"
        assert resolve_payout_account(session, mixed_wallet) == wallet.address


def test_create_bounty_rejects_non_positive_issue_number(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        for issue_number in (0, -1):
            with pytest.raises(LedgerError, match="issue_number must be positive"):
                create_bounty(
                    session,
                    repo="ramimbo/mergework",
                    issue_number=issue_number,
                    issue_url=f"https://github.com/ramimbo/mergework/issues/{issue_number}",
                    title="Invalid bounty",
                    reward_mrwk="1",
                    acceptance="Should not be created",
                )


def test_create_bounty_rejects_duplicate_repo_issue(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=7,
            issue_url="https://github.com/ramimbo/mergework/issues/7",
            title="Original bounty",
            reward_mrwk="25",
            acceptance="First bounty for this issue.",
        )

        with pytest.raises(LedgerError, match="bounty already exists for issue"):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=7,
                issue_url="https://github.com/ramimbo/mergework/issues/7",
                title="Duplicate bounty",
                reward_mrwk="25",
                acceptance="Second bounty for this issue should be rejected cleanly.",
            )


def test_multi_award_bounty_pays_distinct_submissions_until_exhausted(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=10,
            issue_url="https://github.com/ramimbo/mergework/issues/10",
            title="Review multiple PRs",
            reward_mrwk="25",
            max_awards=3,
            acceptance="Each accepted PR review can earn one award.",
        )
        reserve_account = reserve_account_for_bounty(bounty.id)

        assert bounty.reward_microunits == 25_000_000
        assert bounty.reserved_microunits == 75_000_000
        assert bounty.max_awards == 3
        assert bounty.awards_paid == 0
        assert get_balance(session, reserve_account) == 75_000_000

        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/10",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        assert bounty.status == "open"
        assert bounty.awards_paid == 1
        assert get_balance(session, reserve_account) == 50_000_000

        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/11",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:carol",
            submission_url="https://github.com/ramimbo/mergework/pull/12",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        assert bounty.status == "paid"
        assert bounty.awards_paid == 3
        assert get_balance(session, reserve_account) == 0
        assert get_balance(session, "github:alice") == 25_000_000
        assert get_balance(session, "github:bob") == 25_000_000
        assert get_balance(session, "github:carol") == 25_000_000
        with pytest.raises(LedgerError, match="already paid"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:dana",
                submission_url="https://github.com/ramimbo/mergework/pull/13",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
            )
        assert verify_hash_chain(session) is True
        assert verify_supply_conservation(session) is True


def test_multi_award_bounty_rejects_duplicate_submission_url(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=11,
            issue_url="https://github.com/ramimbo/mergework/issues/11",
            title="Repeated proof guard",
            reward_mrwk="10",
            max_awards=2,
            acceptance="Each distinct accepted proof can earn one award.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/11",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        with pytest.raises(LedgerError, match="submission already paid"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:bob",
                submission_url="https://github.com/ramimbo/mergework/pull/11",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted", "delivery": "second"},
            )

        assert bounty.status == "open"
        assert bounty.awards_paid == 1
        assert get_balance(session, "github:bob") == 0


def test_close_bounty_releases_unpaid_awards(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=13,
            issue_url="https://github.com/ramimbo/mergework/issues/13",
            title="Close unused awards",
            reward_mrwk="10",
            max_awards=3,
            acceptance="Each accepted proof can earn one award.",
        )
        reserve_account = reserve_account_for_bounty(bounty.id)
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/13",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        release = close_bounty(
            session,
            bounty_id=bounty.id,
            closed_by="maintainer",
            reference="https://github.com/ramimbo/mergework/issues/13#close",
        )

        assert release is not None
        assert release.entry_type == "bounty_release"
        assert release.amount_microunits == 20_000_000
        assert bounty.status == "closed"
        assert bounty.awards_paid == 1
        assert get_balance(session, reserve_account) == 0
        assert get_balance(session, "github:alice") == 10_000_000
        with pytest.raises(LedgerError, match="bounty is not open"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:bob",
                submission_url="https://github.com/ramimbo/mergework/pull/14",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
            )
        assert verify_hash_chain(session) is True
        assert verify_supply_conservation(session) is True


def test_payout_is_idempotent_for_same_bounty(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=9,
            issue_url="https://github.com/ramimbo/mergework/issues/9",
            title="Fix docs",
            reward_mrwk="50",
            acceptance="Accepted label",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/10",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        with pytest.raises(LedgerError, match="already paid"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:bob",
                submission_url="https://github.com/ramimbo/mergework/pull/10",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
            )


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


def test_bounty_max_awards_must_be_positive(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        with pytest.raises(LedgerError, match="max_awards must be positive"):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=12,
                issue_url="https://github.com/ramimbo/mergework/issues/12",
                title="Invalid award count",
                reward_mrwk="10",
                max_awards=0,
                acceptance="Accepted label",
            )


def test_create_schema_migrates_existing_bounty_award_columns(sqlite_url: str) -> None:
    engine = make_engine(sqlite_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE bounties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo VARCHAR(200) NOT NULL,
                issue_number INTEGER NOT NULL,
                issue_url VARCHAR(500) NOT NULL,
                title VARCHAR(300) NOT NULL,
                reward_microunits INTEGER NOT NULL,
                reserved_microunits INTEGER NOT NULL,
                status VARCHAR(40) NOT NULL,
                acceptance TEXT NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO bounties (
                repo, issue_number, issue_url, title, reward_microunits,
                reserved_microunits, status, acceptance, created_at
            ) VALUES (
                'ramimbo/mergework', 1,
                'https://github.com/ramimbo/mergework/issues/1',
                'Old paid bounty', 25000000, 25000000, 'paid',
                'Accepted label', '2026-05-23 00:00:00'
            )
            """
        )
    engine.dispose()

    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        bounty = session.get(Bounty, 1)
        assert bounty is not None
        assert bounty.max_awards == 1
        assert bounty.awards_paid == 1


def test_hash_chain_detects_tampering(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        entry = session.get(LedgerEntry, 1)
        assert entry is not None
        entry.amount_microunits = 1

        assert verify_hash_chain(session) is False


# --- Exhausted round overflow detection tests ---


def test_exhausted_round_overflow_detects_unpaid_accepted_submissions(sqlite_url: str) -> None:
    """When max_awards=2 and 3 accepted submissions exist but only 2 paid, overflow should detect 1."""
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=281,
            issue_url="https://github.com/ramimbo/mergework/issues/281",
            title="Exhausted round overflow",
            reward_mrwk="12",
            max_awards=2,
            acceptance="Maintainer applies mrwk:accepted.",
        )

        # Pay 2 accepted submissions (fills all award slots)
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/281-a",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/281-b",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        # Add a 3rd accepted submission that wasn't paid (overflow)
        session.add(
            Submission(
                bounty_id=bounty.id,
                submitter_account="github:carol",
                url="https://github.com/ramimbo/mergework/pull/281-c",
                status="accepted",
                verifier_result=canonical_json({"label": "mrwk:accepted"}),
            )
        )
        session.flush()

        # Close the bounty so it's considered exhausted
        bounty = session.get(Bounty, bounty.id)
        bounty.status = "closed"
        session.flush()

        overflows = exhausted_round_overflow_detection(session)
        summary = overflow_summary(overflows)

        assert summary["exhausted_rounds_with_overflow"] == 1
        assert summary["total_overflow_submissions"] == 1
        assert len(overflows) == 1
        assert overflows[0].bounty_id == bounty.id
        assert overflows[0].total_awards == 2
        assert overflows[0].awards_paid == 2
        assert len(overflows[0].overflow_submissions) == 1
        assert overflows[0].overflow_submissions[0].submitter_account == "github:carol"
        assert overflows[0].overflow_submissions[0].submission_url == "https://github.com/ramimbo/mergework/pull/281-c"


def test_exhausted_round_no_overflow_when_submissions_within_awards(sqlite_url: str) -> None:
    """When max_awards=3 and only 2 accepted submissions, there should be no overflow."""
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=282,
            issue_url="https://github.com/ramimbo/mergework/issues/282",
            title="No overflow case",
            reward_mrwk="12",
            max_awards=3,
            acceptance="Maintainer applies mrwk:accepted.",
        )

        # Pay 2 accepted submissions (within the 3-award limit)
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/282-a",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/282-b",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        # Close the bounty (exhausted but no overflow since awards > submissions)
        bounty = session.get(Bounty, bounty.id)
        bounty.status = "closed"
        session.flush()

        overflows = exhausted_round_overflow_detection(session)
        summary = overflow_summary(overflows)

        assert summary["exhausted_rounds_with_overflow"] == 0
        assert summary["total_overflow_submissions"] == 0
        assert len(overflows) == 0


def test_exhausted_round_multiple_bounties_mixed(sqlite_url: str) -> None:
    """Multiple bounties, only one with overflow."""
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

        # Bounty 1: max_awards=2, paid 2, has 3 accepted (1 overflow)
        bounty1 = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=283,
            issue_url="https://github.com/ramimbo/mergework/issues/283",
            title="Overflow bounty",
            reward_mrwk="12",
            max_awards=2,
            acceptance="Maintainer applies mrwk:accepted.",
        )
        pay_bounty(
            session,
            bounty_id=bounty1.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/283-a",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        pay_bounty(
            session,
            bounty_id=bounty1.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/283-b",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        session.add(
            Submission(
                bounty_id=bounty1.id,
                submitter_account="github:carol",
                url="https://github.com/ramimbo/mergework/pull/283-c",
                status="accepted",
                verifier_result=canonical_json({"label": "mrwk:accepted"}),
            )
        )
        bounty1 = session.get(Bounty, bounty1.id)
        bounty1.status = "closed"
        session.flush()

        # Bounty 2: max_awards=3, paid 2, has 2 accepted (no overflow)
        bounty2 = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=284,
            issue_url="https://github.com/ramimbo/mergework/issues/284",
            title="Clean bounty",
            reward_mrwk="12",
            max_awards=3,
            acceptance="Maintainer applies mrwk:accepted.",
        )
        pay_bounty(
            session,
            bounty_id=bounty2.id,
            to_account="github:dave",
            submission_url="https://github.com/ramimbo/mergework/pull/284-a",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        pay_bounty(
            session,
            bounty_id=bounty2.id,
            to_account="github:eve",
            submission_url="https://github.com/ramimbo/mergework/pull/284-b",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        bounty2 = session.get(Bounty, bounty2.id)
        bounty2.status = "paid"
        session.flush()

        overflows = exhausted_round_overflow_detection(session)
        summary = overflow_summary(overflows)

        assert summary["exhausted_rounds_with_overflow"] == 1
        assert summary["total_overflow_submissions"] == 1
        assert len(overflows) == 1
        assert overflows[0].bounty_id == bounty1.id
        assert overflows[0].total_awards == 2
        assert overflows[0].awards_paid == 2
        assert len(overflows[0].overflow_submissions) == 1
        assert overflows[0].overflow_submissions[0].submitter_account == "github:carol"
