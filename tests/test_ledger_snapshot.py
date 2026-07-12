from __future__ import annotations

import json
from datetime import UTC, datetime

from app.db import create_schema, session_scope
from app.ledger.service import (
    GENESIS_SUPPLY_MICRO,
    TREASURY_ACCOUNT,
    add_ledger_entry,
    create_bounty,
    ensure_genesis,
    pay_bounty,
)
from app.ledger.snapshot import (
    LEDGER_SNAPSHOT_SCHEMA,
    LEDGER_SNAPSHOT_SCHEMA_VERSION,
    ledger_snapshot,
    ledger_snapshot_account_proof,
    ledger_snapshot_json,
    ledger_snapshot_schema_json,
    verify_ledger_snapshot_account_proof,
)
from app.models import LedgerEntry
from scripts.export_ledger_snapshot import main as export_ledger_snapshot_main
from scripts.export_ledger_snapshot import read_only_session_scope


def test_ledger_snapshot_exports_deterministic_integer_balances(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    generated_at = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=764,
            issue_url="https://github.com/ramimbo/mergework/issues/764",
            title="Snapshot exporter",
            reward_mrwk="12.5",
            max_awards=2,
            acceptance="Focused read-only ledger snapshot exporter.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:alice",
            submission_url="https://github.com/ramimbo/mergework/pull/800",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )

        first = ledger_snapshot(
            session,
            generated_at=generated_at,
            source_mode="test",
            source_host="https://mrwk.example",
        )
        second = ledger_snapshot(
            session,
            generated_at=generated_at,
            source_mode="test",
            source_host="https://mrwk.example",
        )

    first_json = ledger_snapshot_json(first)
    second_json = ledger_snapshot_json(second)

    assert first == second
    assert first_json == second_json
    assert first_json.endswith("\n")
    assert json.loads(first_json) == first
    assert first["schema"] == LEDGER_SNAPSHOT_SCHEMA
    assert first["schema_version"] == LEDGER_SNAPSHOT_SCHEMA_VERSION
    assert first["generated_at"] == "2026-06-02T12:00:00.000000Z"
    assert first["source"] == {"mode": "test", "host": "https://mrwk.example"}
    assert first["proposal_validation"]["status"] == "partial"
    assert first["genesis_supply_microunits"] == GENESIS_SUPPLY_MICRO
    assert first["ledger_anchor"]["latest_sequence"] == 3
    assert isinstance(first["ledger_anchor"]["latest_entry_hash"], str)
    assert first["merkle"]["hash_algorithm"] == "sha256"
    assert first["merkle"]["account_count"] == 3
    assert first["merkle"]["root"]
    assert first["merkle"]["account_root"]
    assert first["verification"] == {
        "hash_chain_ok": True,
        "supply_conservation_ok": True,
    }
    assert first["totals"] == {
        "credited_microunits": GENESIS_SUPPLY_MICRO + 25_000_000 + 12_500_000,
        "debited_microunits": 25_000_000 + 12_500_000,
        "net_supply_microunits": GENESIS_SUPPLY_MICRO,
    }
    assert first["accounts"] == [
        {"account": "github:alice", "balance_microunits": 12_500_000},
        {"account": "reserve:bounty:1", "balance_microunits": 12_500_000},
        {
            "account": TREASURY_ACCOUNT,
            "balance_microunits": GENESIS_SUPPLY_MICRO - 25_000_000,
        },
    ]
    assert all(isinstance(row["balance_microunits"], int) for row in first["accounts"])


def test_ledger_snapshot_merkle_root_ignores_generated_at_and_source(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        first = ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
            source_mode="database",
            source_host="https://one.example",
        )
        second = ledger_snapshot(
            session,
            generated_at=datetime(2026, 6, 3, 12, 0, tzinfo=UTC),
            source_mode="api",
            source_host="https://two.example",
        )

    assert first["generated_at"] != second["generated_at"]
    assert first["source"] != second["source"]
    assert first["merkle"] == second["merkle"]


def test_ledger_snapshot_account_proof_verifies_and_rejects_tampering(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        add_ledger_entry(
            session,
            entry_type="test_payment",
            from_account=TREASURY_ACCOUNT,
            to_account="github:alice",
            amount_microunits=3_000_000,
            reference="test:alice",
        )
        add_ledger_entry(
            session,
            entry_type="test_payment",
            from_account=TREASURY_ACCOUNT,
            to_account="github:bob",
            amount_microunits=2_000_000,
            reference="test:bob",
        )
        snapshot = ledger_snapshot(session, generated_at=datetime(2026, 6, 2, tzinfo=UTC))

    proof = ledger_snapshot_account_proof(snapshot, "github:alice")

    assert proof["schema"] == "mergework.ledger_snapshot_account_proof.v1"
    assert proof["account"] == "github:alice"
    assert proof["balance_microunits"] == 3_000_000
    assert proof["account_count"] == 3
    assert proof["root"] == snapshot["merkle"]["root"]
    expected_root = snapshot["merkle"]["root"]
    assert verify_ledger_snapshot_account_proof(proof, expected_root=expected_root) is True

    tampered_balance = {**proof, "balance_microunits": 4_000_000}
    tampered_anchor = {
        **proof,
        "ledger_anchor": {**proof["ledger_anchor"], "latest_sequence": 99},
    }
    tampered_sibling = {**proof, "siblings": [{**proof["siblings"][0], "hash": "0" * 64}]}

    assert (
        verify_ledger_snapshot_account_proof(tampered_balance, expected_root=expected_root) is False
    )
    assert (
        verify_ledger_snapshot_account_proof(tampered_anchor, expected_root=expected_root) is False
    )
    assert (
        verify_ledger_snapshot_account_proof(tampered_sibling, expected_root=expected_root) is False
    )

    tampered_schema = {**proof, "schema": "mergework.ledger_snapshot_account_proof.v0"}
    tampered_schema_version = {**proof, "schema_version": proof["schema_version"] + 1}
    tampered_hash_algorithm = {**proof, "hash_algorithm": "sha512"}
    tampered_account = {**proof, "account": "github:bob"}

    assert (
        verify_ledger_snapshot_account_proof(tampered_schema, expected_root=expected_root) is False
    )
    assert (
        verify_ledger_snapshot_account_proof(tampered_schema_version, expected_root=expected_root)
        is False
    )
    assert (
        verify_ledger_snapshot_account_proof(tampered_hash_algorithm, expected_root=expected_root)
        is False
    )
    assert (
        verify_ledger_snapshot_account_proof(tampered_account, expected_root=expected_root) is False
    )


def test_ledger_snapshot_single_account_proof_has_empty_path(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        add_ledger_entry(
            session,
            entry_type="test_single",
            from_account=None,
            to_account="github:alice",
            amount_microunits=1,
            reference="test:single",
        )
        snapshot = ledger_snapshot(session, generated_at=datetime(2026, 6, 2, tzinfo=UTC))

    proof = ledger_snapshot_account_proof(snapshot, "github:alice")

    assert snapshot["merkle"]["account_count"] == 1
    assert proof["siblings"] == []
    assert proof["account_root"] == proof["leaf_hash"]
    assert (
        verify_ledger_snapshot_account_proof(proof, expected_root=snapshot["merkle"]["root"])
        is True
    )


def test_ledger_snapshot_reports_hash_chain_failure(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        entry = session.get(LedgerEntry, 1)
        assert entry is not None
        entry.reference = "tampered-genesis"

        snapshot = ledger_snapshot(session, generated_at=datetime(2026, 6, 2, tzinfo=UTC))

    assert snapshot["verification"]["hash_chain_ok"] is False
    assert snapshot["verification"]["supply_conservation_ok"] is True


def test_ledger_snapshot_reports_supply_conservation_failure(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        add_ledger_entry(
            session,
            entry_type="test_airdrop",
            from_account=None,
            to_account="github:alice",
            amount_microunits=1,
            reference="test-airdrop",
        )

        snapshot = ledger_snapshot(session, generated_at=datetime(2026, 6, 2, tzinfo=UTC))

    assert snapshot["verification"]["hash_chain_ok"] is True
    assert snapshot["verification"]["supply_conservation_ok"] is False
    assert snapshot["totals"]["net_supply_microunits"] == GENESIS_SUPPLY_MICRO + 1


def test_ledger_snapshot_handles_empty_ledger(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        snapshot = ledger_snapshot(session, generated_at=datetime(2026, 6, 2, tzinfo=UTC))

    assert snapshot["ledger_anchor"] == {"latest_sequence": 0, "latest_entry_hash": None}
    assert snapshot["accounts"] == []
    assert snapshot["merkle"]["account_count"] == 0
    assert snapshot["merkle"]["root"]
    assert snapshot["merkle"]["account_root"]
    assert snapshot["totals"] == {
        "credited_microunits": 0,
        "debited_microunits": 0,
        "net_supply_microunits": 0,
    }
    assert snapshot["verification"] == {
        "hash_chain_ok": True,
        "supply_conservation_ok": False,
    }


def test_exporter_read_only_session_rolls_back_writes(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with read_only_session_scope(sqlite_url) as session:
        ensure_genesis(session)

    with session_scope(sqlite_url) as session:
        assert session.get(LedgerEntry, 1) is None


def test_exporter_main_accepts_schema_argv(capsys) -> None:
    assert export_ledger_snapshot_main(["--schema"]) == 0

    schema = json.loads(capsys.readouterr().out)

    assert schema["$id"] == LEDGER_SNAPSHOT_SCHEMA


def test_exporter_main_accepts_snapshot_argv(sqlite_url: str, capsys) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    assert (
        export_ledger_snapshot_main(
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

    snapshot = json.loads(capsys.readouterr().out)

    assert snapshot["source"] == {"mode": "test", "host": "https://mrwk.example"}
    assert snapshot["ledger_anchor"]["latest_sequence"] == 1
    assert snapshot["verification"]["hash_chain_ok"] is True


def test_exporter_main_prints_and_verifies_account_proof(sqlite_url: str, tmp_path, capsys) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        add_ledger_entry(
            session,
            entry_type="test_payment",
            from_account=TREASURY_ACCOUNT,
            to_account="github:alice",
            amount_microunits=1_000_000,
            reference="test:alice",
        )

    assert (
        export_ledger_snapshot_main(
            [
                "--database-url",
                sqlite_url,
                "--account-proof",
                "github:alice",
            ]
        )
        == 0
    )
    proof = json.loads(capsys.readouterr().out)
    assert verify_ledger_snapshot_account_proof(proof, expected_root=proof["root"]) is True
    assert verify_ledger_snapshot_account_proof(proof, expected_root="0" * 64) is False

    proof_path = tmp_path / "proof.json"
    proof_path.write_text(json.dumps(proof), encoding="utf-8")

    assert (
        export_ledger_snapshot_main(
            ["--verify-account-proof", str(proof_path), "--expected-root", proof["root"]]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "valid": True,
        "expected_root": proof["root"],
    }
    assert (
        export_ledger_snapshot_main(
            ["--verify-account-proof", str(proof_path), "--expected-root", "0" * 64]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "valid": False,
        "expected_root": "0" * 64,
    }


def test_exporter_main_reports_bad_proof_inputs(sqlite_url: str, tmp_path, capsys) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    assert (
        export_ledger_snapshot_main(
            [
                "--database-url",
                sqlite_url,
                "--account-proof",
                "github:missing",
            ]
        )
        == 1
    )
    assert "error: account not found in snapshot" in capsys.readouterr().err

    missing_path = tmp_path / "missing-proof.json"
    assert export_ledger_snapshot_main(["--verify-account-proof", str(missing_path)]) == 1
    assert (
        "error: --expected-root is required with --verify-account-proof" in capsys.readouterr().err
    )

    assert (
        export_ledger_snapshot_main(
            ["--verify-account-proof", str(missing_path), "--expected-root", "0" * 64]
        )
        == 1
    )
    assert "error: could not read proof file:" in capsys.readouterr().err

    malformed_path = tmp_path / "malformed-proof.json"
    malformed_path.write_text("{", encoding="utf-8")
    assert (
        export_ledger_snapshot_main(
            ["--verify-account-proof", str(malformed_path), "--expected-root", "0" * 64]
        )
        == 1
    )
    assert "error: could not read proof file:" in capsys.readouterr().err


def test_ledger_snapshot_schema_is_deterministic_json() -> None:
    schema = json.loads(ledger_snapshot_schema_json())

    assert schema["$id"] == LEDGER_SNAPSHOT_SCHEMA
    assert schema["properties"]["accounts"]["items"]["properties"]["balance_microunits"] == {
        "type": "integer"
    }
    assert schema["properties"]["merkle"]["properties"]["hash_algorithm"] == {"const": "sha256"}
    assert ledger_snapshot_schema_json() == ledger_snapshot_schema_json()
