from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

import pytest

from app.db import create_schema, session_scope
from app.ledger.merkle import (
    MERKLE_ACCOUNT_LEAF_SCHEMA,
    MERKLE_ACCOUNT_PROOF_SCHEMA,
    MERKLE_ROOT_SCHEMA,
    MerkleProofError,
    ledger_snapshot_account_proof,
    ledger_snapshot_account_proof_json,
    ledger_snapshot_merkle_root,
    ledger_snapshot_merkle_root_json,
    verify_ledger_snapshot_account_proof,
)
from app.ledger.service import TREASURY_ACCOUNT, create_bounty, ensure_genesis, pay_bounty
from app.ledger.snapshot import ledger_snapshot
from scripts.export_ledger_merkle_proof import main as export_ledger_merkle_proof_main


def test_merkle_root_ignores_nondeterministic_snapshot_metadata(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=1027,
            issue_url="https://github.com/ramimbo/mergework/issues/1027",
            title="Snapshot proof bounty",
            reward_mrwk="12.5",
            max_awards=2,
            acceptance="Focused read-only Merkle proof helpers.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/1200",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        first = ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
            source_mode="test",
            source_host="https://one.example",
        )
        second = ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 3, 12, 0, tzinfo=UTC),
            source_mode="other",
            source_host="https://two.example",
        )

    first_root = ledger_snapshot_merkle_root(first)
    second_root = ledger_snapshot_merkle_root(second)

    assert first_root == second_root
    assert first_root["schema"] == MERKLE_ROOT_SCHEMA
    assert first_root["ledger_anchor"] == first["ledger_anchor"]
    assert first_root["tree_size"] == len(first["accounts"])
    assert json.loads(ledger_snapshot_merkle_root_json(first)) == first_root
    assert ledger_snapshot_merkle_root_json(first) == ledger_snapshot_merkle_root_json(second)


def test_merkle_root_defines_empty_and_single_leaf_behavior(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        empty_snapshot = ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 2, tzinfo=UTC),
        )
        ensure_genesis(session)
        single_leaf_snapshot = ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 2, tzinfo=UTC),
        )

    empty_root = ledger_snapshot_merkle_root(empty_snapshot)
    single_root = ledger_snapshot_merkle_root(single_leaf_snapshot)
    single_proof = ledger_snapshot_account_proof(single_leaf_snapshot, TREASURY_ACCOUNT)

    assert empty_root["tree_size"] == 0
    assert len(empty_root["merkle_tree_hash"]) == 64
    assert len(empty_root["root_hash"]) == 64
    assert single_root["tree_size"] == 1
    assert single_proof["proof"] == []
    assert verify_ledger_snapshot_account_proof(single_proof, root=single_root) is True
    with pytest.raises(MerkleProofError, match="account is not present"):
        ledger_snapshot_account_proof(empty_snapshot, TREASURY_ACCOUNT)


def test_merkle_account_proof_verifies_and_serializes_deterministically(
    sqlite_url: str,
) -> None:
    snapshot = _multi_account_snapshot(sqlite_url)

    root = ledger_snapshot_merkle_root(snapshot)
    proof = ledger_snapshot_account_proof(snapshot, "github:alice")
    proof_json = ledger_snapshot_account_proof_json(snapshot, "github:alice")

    assert proof["schema"] == MERKLE_ACCOUNT_PROOF_SCHEMA
    assert proof["root"] == root
    assert proof["leaf"]["schema"] == MERKLE_ACCOUNT_LEAF_SCHEMA
    assert proof["leaf"]["account"] == "github:alice"
    assert proof["leaf"]["balance_microunits"] == 12_500_000
    assert len(proof["proof"]) >= 1
    assert verify_ledger_snapshot_account_proof(proof, root=root) is True
    assert json.loads(proof_json) == proof
    assert proof_json == ledger_snapshot_account_proof_json(snapshot, "github:alice")


def test_merkle_proof_rejects_tampered_leaf_siblings_root_and_anchor(sqlite_url: str) -> None:
    snapshot = _multi_account_snapshot(sqlite_url)
    root = ledger_snapshot_merkle_root(snapshot)
    proof = ledger_snapshot_account_proof(snapshot, "github:alice")

    tampered_account = copy.deepcopy(proof)
    tampered_account["leaf"]["account"] = "github:bob"
    assert verify_ledger_snapshot_account_proof(tampered_account, root=root) is False

    tampered_balance = copy.deepcopy(proof)
    tampered_balance["leaf"]["balance_microunits"] += 1
    assert verify_ledger_snapshot_account_proof(tampered_balance, root=root) is False

    tampered_sibling_hash = copy.deepcopy(proof)
    tampered_sibling_hash["proof"][0]["hash"] = "0" * 64
    assert verify_ledger_snapshot_account_proof(tampered_sibling_hash, root=root) is False

    tampered_direction = copy.deepcopy(proof)
    tampered_direction["proof"][0]["position"] = (
        "left" if proof["proof"][0]["position"] == "right" else "right"
    )
    assert verify_ledger_snapshot_account_proof(tampered_direction, root=root) is False

    tampered_root = copy.deepcopy(root)
    tampered_root["root_hash"] = "1" * 64
    assert verify_ledger_snapshot_account_proof(proof, root=tampered_root) is False

    tampered_anchor = copy.deepcopy(proof)
    tampered_anchor["root"]["ledger_anchor"]["latest_sequence"] += 1
    assert verify_ledger_snapshot_account_proof(tampered_anchor, root=root) is False
    assert verify_ledger_snapshot_account_proof(tampered_anchor) is False

    tampered_tree_size = copy.deepcopy(proof)
    tampered_tree_size["root"]["tree_size"] += 1
    assert verify_ledger_snapshot_account_proof(tampered_tree_size) is False


def test_export_merkle_script_outputs_root_or_account_proof(
    sqlite_url: str,
    capsys,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    assert (
        export_ledger_merkle_proof_main(
            [
                "--database-url",
                sqlite_url,
                "--source-host",
                "https://mrwk.example",
                "--source-mode",
                "test",
            ]
        )
        == 0
    )
    root = json.loads(capsys.readouterr().out)

    assert root["schema"] == MERKLE_ROOT_SCHEMA
    assert root["tree_size"] == 1

    assert (
        export_ledger_merkle_proof_main(
            [
                "--database-url",
                sqlite_url,
                "--source-host",
                "https://mrwk.example",
                "--source-mode",
                "test",
                "--account",
                TREASURY_ACCOUNT,
            ]
        )
        == 0
    )
    proof = json.loads(capsys.readouterr().out)

    assert proof["schema"] == MERKLE_ACCOUNT_PROOF_SCHEMA
    assert verify_ledger_snapshot_account_proof(proof, root=root) is True


def _multi_account_snapshot(sqlite_url: str) -> dict:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=1027,
            issue_url="https://github.com/ramimbo/mergework/issues/1027",
            title="Snapshot proof bounty",
            reward_mrwk="12.5",
            max_awards=3,
            acceptance="Focused read-only Merkle proof helpers.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/1200",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:bob",
            submission_url="https://github.com/ramimbo/mergework/pull/1201",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        return ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
            source_mode="test",
            source_host="https://mrwk.example",
        )
