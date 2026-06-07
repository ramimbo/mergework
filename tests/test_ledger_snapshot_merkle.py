from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from app.db import create_schema, session_scope
from app.ledger.merkle import (
    SNAPSHOT_MERKLE_ACCOUNT_LEAF_SCHEMA,
    SNAPSHOT_MERKLE_ROOT_SCHEMA,
    SnapshotMerkleError,
    snapshot_account_proof,
    snapshot_merkle_root,
    snapshot_merkle_root_json,
    verify_snapshot_account_proof,
)
from app.ledger.service import (
    GENESIS_SUPPLY_MICRO,
    TREASURY_ACCOUNT,
    create_bounty,
    ensure_genesis,
    pay_bounty,
)
from app.ledger.snapshot import ledger_snapshot
from scripts.export_ledger_snapshot_merkle import main as export_ledger_snapshot_merkle_main


def test_snapshot_merkle_root_handles_empty_snapshot(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        snapshot = ledger_snapshot(session, generated_at=datetime(2026, 6, 2, tzinfo=UTC))

    root = snapshot_merkle_root(snapshot)

    assert root == snapshot_merkle_root(snapshot)
    assert root["schema"] == SNAPSHOT_MERKLE_ROOT_SCHEMA
    assert root["tree_size"] == 0
    assert root["ledger_anchor"] == {"latest_sequence": 0, "latest_entry_hash": None}
    assert len(root["account_tree_hash"]) == 64
    assert len(root["root_hash"]) == 64
    assert json.loads(snapshot_merkle_root_json(root)) == root


def test_snapshot_merkle_proof_handles_single_account(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        snapshot = ledger_snapshot(session, generated_at=datetime(2026, 6, 2, tzinfo=UTC))

    root = snapshot_merkle_root(snapshot)
    proof = snapshot_account_proof(snapshot, TREASURY_ACCOUNT)

    assert root == snapshot_merkle_root(snapshot)
    assert proof["leaf"] == {
        "schema": SNAPSHOT_MERKLE_ACCOUNT_LEAF_SCHEMA,
        "schema_version": 1,
        "account": TREASURY_ACCOUNT,
        "balance_microunits": GENESIS_SUPPLY_MICRO,
    }
    assert proof["leaf_index"] == 0
    assert proof["sibling_path"] == []
    assert verify_snapshot_account_proof(proof)
    assert verify_snapshot_account_proof(proof, root)


def test_snapshot_merkle_root_is_metadata_independent_for_multi_account_snapshot(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=1027,
            issue_url="https://github.com/ramimbo/mergework/issues/1027",
            title="Snapshot Merkle proofs",
            reward_mrwk="12.5",
            max_awards=2,
            acceptance="Deterministic read-only snapshot proofs.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/1100",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        first = ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 2, tzinfo=UTC),
            source_mode="database",
            source_host="https://mrwk.example",
        )
        second = ledger_snapshot(
            session,
            generated_at=datetime(2027, 7, 3, tzinfo=UTC),
            source_mode="archive",
            source_host="https://archive.example",
        )

    assert first["generated_at"] != second["generated_at"]
    assert first["source"] != second["source"]

    first_root = snapshot_merkle_root(first)
    second_root = snapshot_merkle_root(second)
    proof = snapshot_account_proof(first, "github:alice")

    assert first_root == second_root
    assert first_root["tree_size"] == 3
    assert proof["leaf_index"] == 0
    assert len(proof["sibling_path"]) == 2
    assert verify_snapshot_account_proof(proof)
    assert verify_snapshot_account_proof(proof, first_root)


def test_snapshot_merkle_proof_rejects_tampering(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=1027,
            issue_url="https://github.com/ramimbo/mergework/issues/1027",
            title="Snapshot Merkle proofs",
            reward_mrwk="12.5",
            max_awards=2,
            acceptance="Deterministic read-only snapshot proofs.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/1100",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        snapshot = ledger_snapshot(session, generated_at=datetime(2026, 6, 2, tzinfo=UTC))

    proof = snapshot_account_proof(snapshot, "github:alice")

    tamper_cases = [
        ("account", lambda item: item["leaf"].__setitem__("account", "github:bob")),
        (
            "balance",
            lambda item: item["leaf"].__setitem__(
                "balance_microunits",
                item["leaf"]["balance_microunits"] + 1,
            ),
        ),
        ("leaf index", lambda item: item.__setitem__("leaf_index", 1)),
        ("sibling hash", lambda item: item["sibling_path"][0].__setitem__("hash", "0" * 64)),
        (
            "sibling direction",
            lambda item: item["sibling_path"][0].__setitem__("direction", "left"),
        ),
        (
            "sibling order",
            lambda item: item.__setitem__(
                "sibling_path",
                list(reversed(item["sibling_path"])),
            ),
        ),
        ("proof tree size", lambda item: item.__setitem__("tree_size", item["tree_size"] + 1)),
        (
            "root tree size",
            lambda item: item["root"].__setitem__(
                "tree_size",
                item["root"]["tree_size"] + 1,
            ),
        ),
        ("root hash", lambda item: item["root"].__setitem__("root_hash", "0" * 64)),
        (
            "ledger anchor",
            lambda item: item["root"]["ledger_anchor"].__setitem__("latest_sequence", 99),
        ),
    ]

    for name, mutate in tamper_cases:
        tampered = deepcopy(proof)
        mutate(tampered)
        assert not verify_snapshot_account_proof(tampered), name


def test_snapshot_merkle_proof_raises_for_missing_account(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        snapshot = ledger_snapshot(session, generated_at=datetime(2026, 6, 2, tzinfo=UTC))

    with pytest.raises(SnapshotMerkleError, match="account not found"):
        snapshot_account_proof(snapshot, "github:missing")


def test_snapshot_merkle_exporter_outputs_root_and_proof(
    sqlite_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    root_args = [
        "--database-url",
        sqlite_url,
        "--source-host",
        "https://mrwk.example",
        "--source-mode",
        "test",
    ]
    assert export_ledger_snapshot_merkle_main(root_args) == 0
    root = json.loads(capsys.readouterr().out)

    assert root["tree_size"] == 1

    proof_args = [
        *root_args,
        "--account",
        TREASURY_ACCOUNT,
    ]
    assert export_ledger_snapshot_merkle_main(proof_args) == 0
    proof = json.loads(capsys.readouterr().out)

    assert verify_snapshot_account_proof(proof, root)


def test_snapshot_merkle_verifier_rejects_expected_root_mismatch(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        snapshot = ledger_snapshot(session, generated_at=datetime(2026, 6, 2, tzinfo=UTC))

    proof = snapshot_account_proof(snapshot, TREASURY_ACCOUNT)
    other_root: dict[str, Any] = deepcopy(proof["root"])
    other_root["ledger_anchor"] = {
        "latest_sequence": 2,
        "latest_entry_hash": other_root["ledger_anchor"]["latest_entry_hash"],
    }

    assert verify_snapshot_account_proof(proof)
    assert not verify_snapshot_account_proof(proof, other_root)
