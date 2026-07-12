from __future__ import annotations

import hashlib
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
LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA = "mergework.ledger_snapshot_account_proof.v1"
LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA_VERSION = 1
MERKLE_HASH_ALGORITHM = "sha256"
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
        "merkle",
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
        "merkle": {
            "type": "object",
            "required": [
                "hash_algorithm",
                "root",
                "account_root",
                "account_count",
                "root_format",
                "leaf_format",
                "proof_format",
            ],
            "properties": {
                "hash_algorithm": {"const": MERKLE_HASH_ALGORITHM},
                "root": {"type": "string"},
                "account_root": {"type": "string"},
                "account_count": {"type": "integer"},
                "root_format": {"type": "string"},
                "leaf_format": {"type": "string"},
                "proof_format": {"type": "string"},
            },
            "additionalProperties": False,
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
    accounts = _account_balances(entries)
    ledger_anchor = {
        "latest_sequence": latest_entry.sequence if latest_entry else 0,
        "latest_entry_hash": latest_entry.entry_hash if latest_entry else None,
    }
    merkle = ledger_snapshot_merkle(accounts=accounts, ledger_anchor=ledger_anchor)
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
        "ledger_anchor": ledger_anchor,
        "genesis_supply_microunits": GENESIS_SUPPLY_MICRO,
        "accounts": accounts,
        "merkle": {
            "hash_algorithm": MERKLE_HASH_ALGORITHM,
            "root": merkle["root"],
            "account_root": merkle["account_root"],
            "account_count": merkle["account_count"],
            "root_format": "mergework.snapshot_merkle.root.v1",
            "leaf_format": "mergework.snapshot_merkle.account_leaf.v1",
            "proof_format": LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA,
        },
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


def ledger_snapshot_account_proof(snapshot: dict[str, Any], account: str) -> dict[str, Any]:
    accounts = _snapshot_accounts(snapshot)
    ledger_anchor = _snapshot_ledger_anchor(snapshot)
    merkle = ledger_snapshot_merkle(accounts=accounts, ledger_anchor=ledger_anchor)
    for index, row in enumerate(accounts):
        if row["account"] != account:
            continue
        proof = _account_proof_from_rows(accounts, index)
        return {
            "schema": LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA,
            "schema_version": LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA_VERSION,
            "hash_algorithm": MERKLE_HASH_ALGORITHM,
            "root": merkle["root"],
            "account_root": proof["account_root"],
            "ledger_anchor": ledger_anchor,
            "account_count": len(accounts),
            "account": row["account"],
            "balance_microunits": row["balance_microunits"],
            "index": index,
            "leaf_hash": proof["leaf_hash"],
            "siblings": proof["siblings"],
        }
    raise ValueError("account not found in snapshot")


def verify_ledger_snapshot_account_proof(proof: dict[str, Any], *, expected_root: str) -> bool:
    """Verify an account proof against a trusted snapshot Merkle root."""
    try:
        if not isinstance(expected_root, str):
            return False
        if proof["schema"] != LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA:
            return False
        if proof["schema_version"] != LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA_VERSION:
            return False
        if proof["hash_algorithm"] != MERKLE_HASH_ALGORITHM:
            return False
        account = str(proof["account"])
        balance_microunits = proof["balance_microunits"]
        index = proof["index"]
        account_count = proof["account_count"]
        ledger_anchor = proof["ledger_anchor"]
        siblings = proof["siblings"]
        if isinstance(balance_microunits, bool) or not isinstance(balance_microunits, int):
            return False
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            return False
        if isinstance(account_count, bool) or not isinstance(account_count, int):
            return False
        if not isinstance(siblings, list):
            return False
        if index >= account_count:
            return False
        leaf_hash = _account_leaf_hash(
            {"account": account, "balance_microunits": balance_microunits}
        )
        if proof["leaf_hash"] != leaf_hash:
            return False
        account_root = leaf_hash
        for sibling in siblings:
            if not isinstance(sibling, dict):
                return False
            position = sibling.get("position")
            sibling_hash = sibling.get("hash")
            if position == "left" and isinstance(sibling_hash, str):
                account_root = _branch_hash(sibling_hash, account_root)
            elif position == "right" and isinstance(sibling_hash, str):
                account_root = _branch_hash(account_root, sibling_hash)
            else:
                return False
        proof_account_root = proof["account_root"]
        if not isinstance(proof_account_root, str) or proof_account_root != account_root:
            return False
        proof_root = proof["root"]
        if not isinstance(proof_root, str):
            return False
        root = _snapshot_root_hash(
            account_root=account_root,
            account_count=account_count,
            ledger_anchor=_clean_ledger_anchor(ledger_anchor),
        )
        return proof_root == root == expected_root
    except (KeyError, TypeError, ValueError):
        return False


def ledger_snapshot_merkle(
    *, accounts: list[dict[str, Any]], ledger_anchor: dict[str, Any]
) -> dict[str, Any]:
    clean_accounts = _sorted_account_rows(accounts)
    account_root = _account_tree_root(clean_accounts)
    clean_anchor = _clean_ledger_anchor(ledger_anchor)
    return {
        "root": _snapshot_root_hash(
            account_root=account_root,
            account_count=len(clean_accounts),
            ledger_anchor=clean_anchor,
        ),
        "account_root": account_root,
        "account_count": len(clean_accounts),
    }


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


def _snapshot_accounts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return _sorted_account_rows(snapshot.get("accounts", []))


def _snapshot_ledger_anchor(snapshot: dict[str, Any]) -> dict[str, Any]:
    return _clean_ledger_anchor(snapshot.get("ledger_anchor", {}))


def _clean_ledger_anchor(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("ledger_anchor must be an object")
    latest_sequence = value.get("latest_sequence")
    latest_entry_hash = value.get("latest_entry_hash")
    if isinstance(latest_sequence, bool) or not isinstance(latest_sequence, int):
        raise ValueError("latest_sequence must be an integer")
    if latest_entry_hash is not None and not isinstance(latest_entry_hash, str):
        raise ValueError("latest_entry_hash must be a string or null")
    return {
        "latest_sequence": latest_sequence,
        "latest_entry_hash": latest_entry_hash,
    }


def _sorted_account_rows(accounts: Any) -> list[dict[str, Any]]:
    if not isinstance(accounts, list):
        raise ValueError("accounts must be a list")
    clean_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in accounts:
        if not isinstance(row, dict):
            raise ValueError("account row must be an object")
        account = row.get("account")
        balance_microunits = row.get("balance_microunits")
        if not isinstance(account, str) or not account:
            raise ValueError("account must be a non-empty string")
        if account in seen:
            raise ValueError("account rows must be unique")
        if isinstance(balance_microunits, bool) or not isinstance(balance_microunits, int):
            raise ValueError("balance_microunits must be an integer")
        seen.add(account)
        clean_rows.append({"account": account, "balance_microunits": balance_microunits})
    return sorted(clean_rows, key=lambda item: item["account"])


def _account_leaf_hash(row: dict[str, Any]) -> str:
    return _hash_payload(
        {
            "domain": "mergework.snapshot_merkle.account_leaf.v1",
            "schema": LEDGER_SNAPSHOT_SCHEMA,
            "schema_version": LEDGER_SNAPSHOT_SCHEMA_VERSION,
            "account": row["account"],
            "balance_microunits": row["balance_microunits"],
        }
    )


def _empty_accounts_root() -> str:
    return _hash_payload(
        {
            "domain": "mergework.snapshot_merkle.empty_accounts.v1",
            "schema": LEDGER_SNAPSHOT_SCHEMA,
            "schema_version": LEDGER_SNAPSHOT_SCHEMA_VERSION,
        }
    )


def _branch_hash(left: str, right: str) -> str:
    return _hash_payload(
        {
            "domain": "mergework.snapshot_merkle.branch.v1",
            "left": left,
            "right": right,
        }
    )


def _snapshot_root_hash(
    *, account_root: str, account_count: int, ledger_anchor: dict[str, Any]
) -> str:
    return _hash_payload(
        {
            "domain": "mergework.snapshot_merkle.root.v1",
            "schema": LEDGER_SNAPSHOT_SCHEMA,
            "schema_version": LEDGER_SNAPSHOT_SCHEMA_VERSION,
            "hash_algorithm": MERKLE_HASH_ALGORITHM,
            "latest_sequence": ledger_anchor["latest_sequence"],
            "latest_entry_hash": ledger_anchor["latest_entry_hash"],
            "account_count": account_count,
            "account_root": account_root,
        }
    )


def _account_tree_root(accounts: list[dict[str, Any]]) -> str:
    if not accounts:
        return _empty_accounts_root()
    level = [_account_leaf_hash(row) for row in accounts]
    while len(level) > 1:
        level = [
            _branch_hash(level[index], level[index + 1]) if index + 1 < len(level) else level[index]
            for index in range(0, len(level), 2)
        ]
    return level[0]


def _account_proof_from_rows(accounts: list[dict[str, Any]], index: int) -> dict[str, Any]:
    level = [_account_leaf_hash(row) for row in accounts]
    leaf_hash = level[index]
    siblings: list[dict[str, str]] = []
    cursor = index
    while len(level) > 1:
        if cursor % 2 == 0:
            sibling_index = cursor + 1
            if sibling_index < len(level):
                siblings.append({"position": "right", "hash": level[sibling_index]})
        else:
            siblings.append({"position": "left", "hash": level[cursor - 1]})
        next_level = [
            _branch_hash(level[node_index], level[node_index + 1])
            if node_index + 1 < len(level)
            else level[node_index]
            for node_index in range(0, len(level), 2)
        ]
        level = next_level
        cursor //= 2
    return {
        "leaf_hash": leaf_hash,
        "account_root": level[0] if level else _empty_accounts_root(),
        "siblings": siblings,
    }


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
