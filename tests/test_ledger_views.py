from __future__ import annotations

from app.db import create_schema, session_scope
from app.ledger.service import add_ledger_entry, create_bounty, ensure_genesis, pay_bounty
from app.ledger_views import (
    account_ledger_transaction_types,
    account_ledger_transactions,
    ledger_entry_to_dict,
    recent_ledger_entries,
)


def test_recent_ledger_entries_attach_payment_proof_hash(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Ledger view helpers",
            reward_mrwk="25",
            acceptance="Ledger views should attach proof hashes consistently.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/320",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        rows = recent_ledger_entries(session, 10)
        detail = ledger_entry_to_dict(session, proof.ledger_sequence)
        missing = ledger_entry_to_dict(session, 9999)

    payment_rows = [row for row in rows if row["sequence"] == proof.ledger_sequence]
    assert len(payment_rows) == 1
    assert payment_rows[0]["proof_hash"] == proof.hash
    assert payment_rows[0]["created_at"].endswith("Z")
    assert detail is not None
    assert detail["proof_hash"] == proof.hash
    assert detail["created_at"].endswith("Z")
    assert missing is None


def test_account_ledger_transactions_filters_both_sides(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Account ledger helper",
            reward_mrwk="25",
            acceptance="Account pages should reuse one ledger transaction helper.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/321",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        manual_entry = add_ledger_entry(
            session,
            entry_type="manual_adjustment",
            from_account="github:alice",
            to_account="github:bob",
            amount_microunits=0,
            reference="manual:account-ledger-helper",
        )

        rows = account_ledger_transactions(session, "github:alice")
        filtered_rows = account_ledger_transactions(
            session, "github:alice", entry_type="manual_adjustment"
        )
        transaction_types = account_ledger_transaction_types(session, "github:alice")

    assert [row["sequence"] for row in rows] == [manual_entry.sequence, proof.ledger_sequence]
    assert [row["sequence"] for row in filtered_rows] == [manual_entry.sequence]
    assert transaction_types == ["bounty_payment", "manual_adjustment"]
    assert rows[0]["from"] == "github:alice"
    assert rows[0]["to"] == "github:bob"
    assert rows[0]["proof_hash"] is None
    assert rows[1]["proof_hash"] == proof.hash
