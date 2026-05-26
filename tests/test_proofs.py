from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty
from app.proofs import (
    mcp_proof_to_dict,
    proof_hash_for_sequence,
    proof_hash_from_path,
    proof_hashes_by_sequence,
    public_proof_payload,
)


def test_proof_hash_from_path_normalizes_and_rejects_malformed_hashes() -> None:
    assert proof_hash_from_path("A" * 64) == "a" * 64

    for proof_hash in (" " + ("a" * 64), "not-a-proof-hash", "g" * 64):
        with pytest.raises(HTTPException) as exc_info:
            proof_hash_from_path(proof_hash)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "proof hash must be 64 hex characters"


def test_proof_helpers_shape_api_and_mcp_payloads(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Proof helper extraction",
            reward_mrwk="25",
            acceptance="Proof lookup helpers should be independently testable.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/320",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        proof_by_sequence = proof_hashes_by_sequence(session, [proof.ledger_sequence, 999])
        single_proof_hash = proof_hash_for_sequence(session, proof.ledger_sequence)
        missing_proof_hash = proof_hash_for_sequence(session, 999)
        api_payload = public_proof_payload(proof)
        mcp_payload = mcp_proof_to_dict(proof)
        empty_lookup = proof_hashes_by_sequence(session, [])

    assert empty_lookup == {}
    assert proof_by_sequence == {proof.ledger_sequence: proof.hash}
    assert single_proof_hash == proof.hash
    assert missing_proof_hash is None
    assert api_payload["kind"] == "bounty_payment"
    assert api_payload["submission_url"] == "https://github.com/ramimbo/mergework/pull/320"
    assert mcp_payload["hash"] == proof.hash
    assert mcp_payload["proof"] == api_payload


def test_proof_payload_helpers_reject_malformed_or_non_object_payloads() -> None:
    for raw_payload in ("{", "[]"):
        proof = SimpleNamespace(
            hash="a" * 64,
            kind="bounty_payment",
            ledger_sequence=1,
            bounty_id=1,
            submission_id=None,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            public_json=raw_payload,
        )

        with pytest.raises(HTTPException) as public_exc_info:
            public_proof_payload(proof)  # type: ignore[arg-type]
        assert public_exc_info.value.status_code == 500
        assert public_exc_info.value.detail == "invalid proof payload"

        with pytest.raises(HTTPException) as mcp_exc_info:
            mcp_proof_to_dict(proof)  # type: ignore[arg-type]
        assert mcp_exc_info.value.status_code == 500
        assert mcp_exc_info.value.detail == "invalid proof payload"
