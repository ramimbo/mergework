from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from app.db import create_schema, session_scope
from app.ledger.service import TREASURY_ACCOUNT, create_bounty, ensure_genesis, pay_bounty
from app.ledger.snapshot import ledger_snapshot
from app.ledger.snapshot_merkle import (
    LEDGER_SNAPSHOT_ACCOUNT_LEAF_SCHEMA,
    LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA,
    LEDGER_SNAPSHOT_MERKLE_ROOT_SCHEMA,
    ledger_snapshot_account_proof,
    ledger_snapshot_account_proof_json,
    ledger_snapshot_merkle_root,
    ledger_snapshot_merkle_root_json,
    verify_ledger_snapshot_account_proof,
)
from scripts.export_ledger_snapshot_merkle import main as export_merkle_main


def test_merkle_root_is_deterministic_and_metadata_independent(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        _seed_multi_account_snapshot(session)
        first_snapshot = ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
            source_mode="database",
            source_host="https://one.example",
        )
        second_snapshot = ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 3, 12, 0, tzinfo=UTC),
            source_mode="fixture",
            source_host="https://two.example",
        )

    first_root = ledger_snapshot_merkle_root(first_snapshot)
    second_root = ledger_snapshot_merkle_root(second_snapshot)

    assert first_snapshot["generated_at"] != second_snapshot["generated_at"]
    assert first_snapshot["source"] != second_snapshot["source"]
    assert first_root == second_root
    assert first_root["schema"] == LEDGER_SNAPSHOT_MERKLE_ROOT_SCHEMA
    assert first_root["snapshot_schema"] == first_snapshot["schema"]
    assert first_root["hash_algorithm"] == "sha256"
    assert first_root["leaf_schema"] == LEDGER_SNAPSHOT_ACCOUNT_LEAF_SCHEMA
    assert first_root["leaf_count"] == len(first_snapshot["accounts"])
    assert first_root["ledger_anchor"] == first_snapshot["ledger_anchor"]
    assert _is_hash(first_root["tree_hash"])
    assert _is_hash(first_root["root_hash"])
    assert json.loads(ledger_snapshot_merkle_root_json(first_root)) == first_root


def test_merkle_root_defines_empty_and_single_leaf_behavior(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        empty_snapshot = ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
        )
        ensure_genesis(session)
        single_snapshot = ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
        )

    empty_root = ledger_snapshot_merkle_root(empty_snapshot)
    single_root = ledger_snapshot_merkle_root(single_snapshot)
    single_proof = ledger_snapshot_account_proof(single_snapshot, TREASURY_ACCOUNT)

    assert empty_root["leaf_count"] == 0
    assert _is_hash(empty_root["tree_hash"])
    assert _is_hash(empty_root["root_hash"])
    assert ledger_snapshot_account_proof(empty_snapshot, TREASURY_ACCOUNT) is None
    assert single_root["leaf_count"] == 1
    assert single_proof is not None
    assert single_proof["siblings"] == []
    assert single_proof["leaf"]["leaf_index"] == 0
    assert verify_ledger_snapshot_account_proof(single_proof) is True


def test_account_merkle_proof_verifies_and_rejects_tampering(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        _seed_multi_account_snapshot(session)
        snapshot = ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
        )

    proof = ledger_snapshot_account_proof(snapshot, "github:alice")
    assert proof is not None
    assert proof["schema"] == LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA
    assert proof["root"] == ledger_snapshot_merkle_root(snapshot)
    assert proof["tree_size"] == len(snapshot["accounts"])
    assert proof["leaf"]["account"] == "github:alice"
    assert proof["leaf"]["balance_microunits"] == 12_500_000
    assert proof["siblings"]
    assert verify_ledger_snapshot_account_proof(proof) is True
    assert json.loads(ledger_snapshot_account_proof_json(proof)) == proof

    tampered_cases = [
        ("account", lambda item: item["leaf"].update({"account": "github:mallory"})),
        ("balance", lambda item: item["leaf"].update({"balance_microunits": 12_500_001})),
        ("leaf_index", lambda item: item.update({"leaf_index": item["leaf_index"] + 1})),
        ("sibling_hash", lambda item: item["siblings"][0].update({"hash": "0" * 64})),
        ("sibling_direction", lambda item: item["siblings"][0].update({"direction": "left"})),
        ("tree_size", lambda item: item.update({"tree_size": item["tree_size"] + 1})),
        ("root_hash", lambda item: item["root"].update({"root_hash": "0" * 64})),
        (
            "ledger_anchor",
            lambda item: item["root"]["ledger_anchor"].update(
                {"latest_sequence": item["root"]["ledger_anchor"]["latest_sequence"] + 1}
            ),
        ),
    ]
    for label, mutate in tampered_cases:
        tampered = deepcopy(proof)
        mutate(tampered)
        assert verify_ledger_snapshot_account_proof(tampered) is False, label


def test_export_merkle_script_prints_root_and_account_proof(sqlite_url: str, capsys) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        _seed_multi_account_snapshot(session)

    assert (
        export_merkle_main(
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

    assert root["schema"] == LEDGER_SNAPSHOT_MERKLE_ROOT_SCHEMA
    assert root["leaf_count"] == 3

    assert (
        export_merkle_main(
            [
                "--database-url",
                sqlite_url,
                "--source-host",
                "https://mrwk.example",
                "--source-mode",
                "test",
                "--account",
                "github:alice",
            ]
        )
        == 0
    )
    proof = json.loads(capsys.readouterr().out)

    assert proof["schema"] == LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA
    assert proof["root"] == root
    assert verify_ledger_snapshot_account_proof(proof) is True


def test_export_merkle_script_rejects_missing_account(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    with pytest.raises(SystemExit):
        export_merkle_main(["--database-url", sqlite_url, "--account", "github:missing"])


def _seed_multi_account_snapshot(session) -> None:
    ensure_genesis(session)
    bounty = create_bounty(
        session,
        repo="ramimbo/mergework",
        issue_number=1027,
        issue_url="https://github.com/ramimbo/mergework/issues/1027",
        title="Snapshot Merkle proof",
        reward_mrwk="12.5",
        max_awards=2,
        acceptance="Focused read-only ledger snapshot Merkle proof tooling.",
    )
    pay_bounty(
        session,
        bounty_id=bounty.id,
        to_account="github:alice",
        submission_url="https://github.com/ramimbo/mergework/pull/1027",
        accepted_by="maintainer",
        verifier_result={"label": "mrwk:accepted"},
    )


def _is_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
