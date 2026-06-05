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
    ledger_snapshot_json,
    ledger_snapshot_schema_json,
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


def test_ledger_snapshot_schema_is_deterministic_json() -> None:
    schema = json.loads(ledger_snapshot_schema_json())

    assert schema["$id"] == LEDGER_SNAPSHOT_SCHEMA
    assert schema["properties"]["accounts"]["items"]["properties"]["balance_microunits"] == {
        "type": "integer"
    }
    assert ledger_snapshot_schema_json() == ledger_snapshot_schema_json()
