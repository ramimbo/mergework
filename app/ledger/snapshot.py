from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger.service import (
    GENESIS_SUPPLY_MICRO,
    canonical_json,
    verify_hash_chain,
    verify_supply_conservation,
)
from app.models import LedgerEntry

LEDGER_SNAPSHOT_SCHEMA = "mergework.ledger_snapshot.v1"
LEDGER_SNAPSHOT_SCHEMA_VERSION = 1
PROPOSAL_VALIDATION_EXPLANATION = (
    "Snapshot verification covers committed ledger entries, the ledger hash chain, "
    "and fixed-supply conservation. It does not replay every historical treasury "
    "proposal, challenge, or governance rule, and it does not treat pending proposals "
    "as committed ledger state."
)

LEDGER_SNAPSHOT_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": LEDGER_SNAPSHOT_SCHEMA,
    "type": "object",
    "required": [
        "schema",
        "schema_version",
        "generated_at",
        "source",
        "proposal_validation",
        "ledger_anchor",
        "genesis_supply_microunits",
        "accounts",
        "totals",
        "verification",
    ],
    "properties": {
        "schema": {"const": LEDGER_SNAPSHOT_SCHEMA},
        "schema_version": {"const": LEDGER_SNAPSHOT_SCHEMA_VERSION},
        "generated_at": {"type": "string"},
        "source": {
            "type": "object",
            "required": ["mode", "host"],
            "properties": {
                "mode": {"type": "string"},
                "host": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "proposal_validation": {
            "type": "object",
            "required": ["status", "explanation"],
            "properties": {
                "status": {"const": "partial"},
                "explanation": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "ledger_anchor": {
            "type": "object",
            "required": ["latest_sequence", "latest_entry_hash"],
            "properties": {
                "latest_sequence": {"type": "integer"},
                "latest_entry_hash": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "genesis_supply_microunits": {"type": "integer"},
        "accounts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["account", "balance_microunits"],
                "properties": {
                    "account": {"type": "string"},
                    "balance_microunits": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
        "totals": {
            "type": "object",
            "required": [
                "credited_microunits",
                "debited_microunits",
                "net_supply_microunits",
            ],
            "properties": {
                "credited_microunits": {"type": "integer"},
                "debited_microunits": {"type": "integer"},
                "net_supply_microunits": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "verification": {
            "type": "object",
            "required": ["hash_chain_ok", "supply_conservation_ok"],
            "properties": {
                "hash_chain_ok": {"type": "boolean"},
                "supply_conservation_ok": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


def ledger_snapshot(
    session: Session,
    *,
    generated_at: datetime | None = None,
    source_mode: str = "database",
    source_host: str | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(UTC)
    entries = list(session.scalars(select(LedgerEntry).order_by(LedgerEntry.sequence)).all())
    latest_entry = entries[-1] if entries else None
    totals = _ledger_totals(entries)
    return {
        "schema": LEDGER_SNAPSHOT_SCHEMA,
        "schema_version": LEDGER_SNAPSHOT_SCHEMA_VERSION,
        "generated_at": _utc_timestamp(generated),
        "source": {
            "mode": source_mode,
            "host": source_host,
        },
        "proposal_validation": {
            "status": "partial",
            "explanation": PROPOSAL_VALIDATION_EXPLANATION,
        },
        "ledger_anchor": {
            "latest_sequence": latest_entry.sequence if latest_entry else 0,
            "latest_entry_hash": latest_entry.entry_hash if latest_entry else None,
        },
        "genesis_supply_microunits": GENESIS_SUPPLY_MICRO,
        "accounts": _account_balances(entries),
        "totals": totals,
        "verification": {
            "hash_chain_ok": verify_hash_chain(session),
            "supply_conservation_ok": verify_supply_conservation(session),
        },
    }


def ledger_snapshot_json(snapshot: dict[str, Any]) -> str:
    return canonical_json(snapshot) + "\n"


def ledger_snapshot_schema_json() -> str:
    return (
        json.dumps(
            LEDGER_SNAPSHOT_JSON_SCHEMA,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    )


def _ledger_totals(entries: list[LedgerEntry]) -> dict[str, int]:
    credited_microunits = sum(entry.amount_microunits for entry in entries)
    debited_microunits = sum(
        entry.amount_microunits for entry in entries if entry.from_account is not None
    )
    return {
        "credited_microunits": credited_microunits,
        "debited_microunits": debited_microunits,
        "net_supply_microunits": credited_microunits - debited_microunits,
    }


def _account_balances(entries: list[LedgerEntry]) -> list[dict[str, Any]]:
    balances: dict[str, int] = {}
    for entry in entries:
        if entry.to_account is not None:
            balances[entry.to_account] = balances.get(entry.to_account, 0) + entry.amount_microunits
        if entry.from_account is not None:
            balances[entry.from_account] = (
                balances.get(entry.from_account, 0) - entry.amount_microunits
            )
    return [
        {"account": account, "balance_microunits": balances[account]}
        for account in sorted(balances)
    ]


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
