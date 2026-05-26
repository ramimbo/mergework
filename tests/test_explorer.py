from __future__ import annotations

from app.db import create_schema, session_scope
from app.explorer import ledger_transactions_for_account, proof_hashes_by_sequence
from app.ledger.service import add_ledger_entry, create_bounty, ensure_genesis, pay_bounty


def test_proof_hashes_by_sequence_handles_empty_and_paid_entries(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Explorer helpers",
            reward_mrwk="25",
            acceptance="Account and wallet pages should share explorer query helpers.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/320",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        assert proof_hashes_by_sequence(session, []) == {}
        assert proof_hashes_by_sequence(session, [proof.ledger_sequence]) == {
            proof.ledger_sequence: proof.hash
        }


def test_ledger_transactions_for_account_orders_and_attaches_proofs(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Explorer transactions",
            reward_mrwk="10",
            acceptance="Transaction helper should preserve proof links.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/321",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        manual = add_ledger_entry(
            session,
            entry_type="wallet_transfer",
            from_account="github:alice",
            to_account="github:bob",
            amount_microunits=1_000_000,
            reference="manual-transfer",
        )

        rows = ledger_transactions_for_account(session, "github:alice")
        limited = ledger_transactions_for_account(session, "github:alice", limit=1)

    assert [row["sequence"] for row in rows] == [manual.sequence, proof.ledger_sequence]
    assert rows[0]["proof_hash"] is None
    assert rows[1]["proof_hash"] == proof.hash
    assert [row["sequence"] for row in limited] == [manual.sequence]
