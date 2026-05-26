from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty
from app.ledger_api import (
    ledger_entries,
    ledger_entry,
    positive_ledger_sequence,
    proof_hash_from_path,
    proof_payload,
)
from app.models import Proof


def test_ledger_api_helpers_shape_ledger_and_proof_payloads(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Ledger API route extraction",
            reward_mrwk="25",
            acceptance="Ledger API helpers should be independently testable.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/320",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        proof_sequence = proof.ledger_sequence
        proof_hash = proof.hash

    entries = ledger_entries(sqlite_url, 10)
    entry = ledger_entry(sqlite_url, proof_sequence)
    payload = proof_payload(sqlite_url, proof_hash)

    assert entries[0]["sequence"] == proof_sequence
    assert entries[0]["proof_hash"] == proof_hash
    assert entry["proof_hash"] == proof_hash
    assert payload["kind"] == "bounty_payment"
    assert payload["submission_url"] == "https://github.com/ramimbo/mergework/pull/320"


def test_ledger_api_helpers_reject_malformed_path_values(sqlite_url: str) -> None:
    assert positive_ledger_sequence(1) == 1
    assert proof_hash_from_path("A" * 64) == "a" * 64

    for sequence, detail in (
        (0, "ledger sequence must be positive"),
        (2**63, "ledger sequence is too large"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            positive_ledger_sequence(sequence)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == detail

    for proof_hash in (" " + ("a" * 64), "not-a-proof-hash", "g" * 64):
        with pytest.raises(HTTPException) as exc_info:
            proof_hash_from_path(proof_hash)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "proof hash must be 64 hex characters"


def test_proof_payload_reports_missing_or_invalid_payloads(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with pytest.raises(HTTPException) as missing_info:
        proof_payload(sqlite_url, "a" * 64)
    assert missing_info.value.status_code == 404
    assert missing_info.value.detail == "proof not found"

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Invalid proof payload",
            reward_mrwk="25",
            acceptance="Ledger API proof payload errors should be stable.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/321",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        proof_hash = proof.hash

    for invalid_payload in ("{", "[]"):
        with session_scope(sqlite_url) as session:
            proof_row = session.get(Proof, proof_hash)
            assert proof_row is not None
            proof_row.public_json = invalid_payload

        with pytest.raises(HTTPException) as invalid_info:
            proof_payload(sqlite_url, proof_hash)
        assert invalid_info.value.status_code == 500
        assert invalid_info.value.detail == "invalid proof payload"
