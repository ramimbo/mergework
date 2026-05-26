from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import create_schema, make_engine, session_scope
from app.ledger.service import (
    GENESIS_SUPPLY_MICRO,
    TREASURY_ACCOUNT,
    LedgerError,
    add_ledger_entry,
    close_bounty,
    create_bounty,
    ensure_genesis,
    find_bounty_by_issue,
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


def test_add_ledger_entry_rejects_malformed_public_fields(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        base_kwargs = {
            "entry_type": "manual_adjustment",
            "from_account": TREASURY_ACCOUNT,
            "to_account": "github:alice",
            "amount_microunits": 1,
            "reference": "manual:test",
        }

        bad_cases = (
            ("entry_type", "manual\nadjustment", "entry_type must not contain control characters"),
            ("entry_type", "   ", "entry_type is required"),
            ("entry_type", "x" * 41, "entry_type is too long"),
            ("from_account", "treasury:\nmrwk", "from_account must not contain control characters"),
            ("from_account", "x" * 129, "from_account is too long"),
            ("to_account", "github:\nalice", "to_account must not contain control characters"),
            ("to_account", "github:" + "a" * 122, "to_account is too long"),
            ("reference", "manual\nref", "reference must not contain control characters"),
            ("reference", "   ", "reference is required"),
            ("reference", "x" * 501, "reference is too long"),
        )
        for field, value, message in bad_cases:
            kwargs = {**base_kwargs, field: value}
            with pytest.raises(LedgerError, match=message):
                add_ledger_entry(session, **kwargs)

        nullable_entry = add_ledger_entry(
            session,
            entry_type=" manual_adjustment ",
            from_account=None,
            to_account=None,
            amount_microunits=0,
            reference=" manual:none ",
        )

        assert nullable_entry.entry_type == "manual_adjustment"
        assert nullable_entry.from_account is None
        assert nullable_entry.to_account is None
        assert nullable_entry.reference == "manual:none"

        balanced_entry = add_ledger_entry(
            session,
            entry_type=" manual_adjustment ",
            from_account=f" {TREASURY_ACCOUNT} ",
            to_account=" github:alice ",
            amount_microunits=1,
            reference=" manual:test ",
        )

        assert balanced_entry.entry_type == "manual_adjustment"
        assert balanced_entry.from_account == TREASURY_ACCOUNT
        assert balanced_entry.to_account == "github:alice"
        assert balanced_entry.reference == "manual:test"
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


@pytest.mark.parametrize("amount_microunits", [True, 1.5, "1"])
def test_add_ledger_entry_rejects_non_integer_amounts(
    sqlite_url: str, amount_microunits: object
) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

        with pytest.raises(LedgerError, match="amount_microunits must be an integer"):
            add_ledger_entry(
                session,
                entry_type="bad_amount",
                from_account=TREASURY_ACCOUNT,
                to_account="github:alice",
                amount_microunits=amount_microunits,  # type: ignore[arg-type]
                reference="test-bad-amount",
            )


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


def test_pay_bounty_rejects_non_json_serializable_verifier_result_values(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=228,
            issue_url="https://github.com/ramimbo/mergework/issues/228",
            title="Reject unserializable verifier metadata",
            reward_mrwk="50",
            acceptance="Accepted payouts must store JSON proof metadata.",
        )
        reserve_account = reserve_account_for_bounty(bounty.id)

        with pytest.raises(LedgerError, match="verifier_result must be JSON serializable"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:alice",
                submission_url="https://github.com/ramimbo/mergework/pull/228",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted", "raw": object()},
            )

        assert bounty.awards_paid == 0
        assert bounty.status == "open"
        assert get_balance(session, "github:alice") == 0
        assert get_balance(session, reserve_account) == 50_000_000
        assert session.query(Submission).count() == 0
        assert session.query(Proof).count() == 0


def test_pay_bounty_rejects_blank_or_control_character_account(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=8,
            issue_url="https://github.com/ramimbo/mergework/issues/8",
            title="Reject malformed payout accounts",
            reward_mrwk="50",
            max_awards=2,
            acceptance="Accepted PRs must record a clean payout account.",
        )

        for account, message in (
            ("   ", "to_account is required"),
            ("github:alice\nbad", "to_account must not contain control characters"),
        ):
            with pytest.raises(LedgerError, match=message):
                pay_bounty(
                    session,
                    bounty_id=bounty.id,
                    to_account=account,
                    submission_url="https://github.com/ramimbo/mergework/pull/8",
                    accepted_by="maintainer",
                    verifier_result={"label": "mrwk:accepted"},
                )

        session.refresh(bounty)
        assert bounty.status == "open"
        assert bounty.awards_paid == 0
        assert get_balance(session, reserve_account_for_bounty(bounty.id)) == 100_000_000
        assert session.scalars(select(Submission)).all() == []
        assert session.scalars(select(Proof)).all() == []

        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account=" github:alice ",
            submission_url="https://github.com/ramimbo/mergework/pull/8",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        submission = session.scalars(select(Submission)).one()

        assert submission.submitter_account == "github:alice"
        assert get_balance(session, "github:alice") == 50_000_000
        assert '"to_account":"github:alice"' in proof.public_json


def test_resolve_payout_account_accepts_mixed_case_prefixes(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        wallet = register_wallet(session, public_key_hex="1" * 64)
        mixed_wallet = "MRWK1" + wallet.address.removeprefix("mrwk1").upper()

        assert resolve_payout_account(session, " GitHub:Alice ") == "github:alice"
        assert resolve_payout_account(session, mixed_wallet) == wallet.address


def test_bounty_service_rejects_non_string_text_fields(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    base_bounty = {
        "repo": "ramimbo/mergework",
        "issue_number": 9,
        "issue_url": "https://github.com/ramimbo/mergework/issues/9",
        "title": "Validate text fields",
        "reward_mrwk": "10",
        "acceptance": "Accepted label",
    }

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        for field, value, message in (
            ("repo", 123, "repo must be a string"),
            ("issue_url", 123, "URL must be a string"),
            ("title", 123, "title must be a string"),
            ("acceptance", 123, "acceptance must be a string"),
        ):
            payload = dict(base_bounty)
            payload[field] = value
            with pytest.raises(LedgerError, match=message):
                create_bounty(session, **payload)

        bounty = create_bounty(session, **base_bounty)

        with pytest.raises(LedgerError, match="URL must be a string"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:alice",
                submission_url=123,
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
            )
        with pytest.raises(LedgerError, match="accepted_by must be a string"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:alice",
                submission_url="https://github.com/ramimbo/mergework/pull/9",
                accepted_by=123,
                verifier_result={"label": "mrwk:accepted"},
            )
        with pytest.raises(LedgerError, match="closed_by must be a string"):
            close_bounty(
                session,
                bounty_id=bounty.id,
                closed_by=123,
                reference="https://github.com/ramimbo/mergework/issues/9#close",
            )
        with pytest.raises(LedgerError, match="URL must be a string"):
            close_bounty(
                session,
                bounty_id=bounty.id,
                closed_by="maintainer",
                reference=123,
            )


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


def test_create_bounty_rejects_oversized_issue_number(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        issue_number = 2**63

        with pytest.raises(LedgerError, match="issue_number is too large"):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=issue_number,
                issue_url=f"https://github.com/ramimbo/mergework/issues/{issue_number}",
                title="Oversized issue bounty",
                reward_mrwk="1",
                acceptance="Should not be created",
            )


def test_create_bounty_rejects_duplicate_repo_issue(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="Ramimbo/MergeWork",
            issue_number=7,
            issue_url="https://github.com/ramimbo/mergework/issues/7",
            title="Original bounty",
            reward_mrwk="25",
            acceptance="First bounty for this issue.",
        )
        assert bounty.repo == "ramimbo/mergework"
        assert find_bounty_by_issue(session, "RAMIMBO/MERGEWORK", 7) == bounty

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


def test_find_bounty_by_issue_matches_legacy_mixed_case_repo(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = Bounty(
            repo="Ramimbo/MergeWork",
            issue_number=77,
            issue_url="https://github.com/ramimbo/mergework/issues/77",
            title="Legacy mixed-case bounty",
            reward_microunits=25_000_000,
            reserved_microunits=25_000_000,
            max_awards=1,
            awards_paid=0,
            status="open",
            acceptance="Legacy rows should still match GitHub repo identity.",
        )
        session.add(bounty)
        session.flush()

        assert find_bounty_by_issue(session, "ramimbo/mergework", 77) == bounty
        assert find_bounty_by_issue(session, "RAMIMBO/MERGEWORK", 77) == bounty


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
