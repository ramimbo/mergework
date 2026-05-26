from __future__ import annotations

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty
from app.payouts import existing_payout_proof_for_submission, payout_response_from_proof


def test_payout_response_from_proof_shapes_admin_api_payload(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Payout helper extraction",
            reward_mrwk="15",
            acceptance="Payout API response helpers should be independently testable.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/320",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        payload = payout_response_from_proof(proof, status="paid")

    assert payload == {
        "status": "paid",
        "bounty_id": bounty.id,
        "to_account": "github:alice",
        "submission_id": proof.submission_id,
        "submission_url": "https://github.com/ramimbo/mergework/pull/320",
        "ledger_sequence": proof.ledger_sequence,
        "ledger_url": f"/ledger/{proof.ledger_sequence}",
        "proof_hash": proof.hash,
        "proof_url": f"/proofs/{proof.hash}",
    }


def test_existing_payout_proof_for_submission_finds_duplicate_proof(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    submission_url = "https://github.com/ramimbo/mergework/pull/321"
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Duplicate payout helper",
            reward_mrwk="15",
            acceptance="Duplicate payout lookup should find the existing proof.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url=submission_url,
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        found = existing_payout_proof_for_submission(session, bounty.id, submission_url)
        missing = existing_payout_proof_for_submission(
            session, bounty.id, "https://github.com/ramimbo/mergework/pull/999"
        )

    assert found is not None
    assert found.hash == proof.hash
    assert missing is None
