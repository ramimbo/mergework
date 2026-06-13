from __future__ import annotations

import hashlib
from typing import Any, cast

from app.ledger.service import canonical_json

LEDGER_SNAPSHOT_MERKLE_ROOT_SCHEMA = "mergework.ledger_snapshot_merkle_root.v1"
LEDGER_SNAPSHOT_MERKLE_ROOT_SCHEMA_VERSION = 1
LEDGER_SNAPSHOT_ACCOUNT_LEAF_SCHEMA = "mergework.ledger_snapshot_account_leaf.v1"
LEDGER_SNAPSHOT_ACCOUNT_LEAF_SCHEMA_VERSION = 1
LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA = "mergework.ledger_snapshot_account_proof.v1"
LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA_VERSION = 1
MERKLE_HASH_ALGORITHM = "sha256"

_EMPTY_TREE_DOMAIN = "mergework.ledger_snapshot_merkle_empty.v1"
_LEAF_HASH_DOMAIN = "mergework.ledger_snapshot_merkle_leaf_hash.v1"
_NODE_HASH_DOMAIN = "mergework.ledger_snapshot_merkle_node.v1"
_ROOT_HASH_DOMAIN = "mergework.ledger_snapshot_merkle_root_hash.v1"
_HASH_HEX_LENGTH = 64


def ledger_snapshot_merkle_root(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the versioned Merkle root object for a Phase 2A ledger snapshot."""
    accounts = _snapshot_accounts(snapshot)
    leaf_hashes = [
        _account_leaf_hash(_account_leaf(row, index)) for index, row in enumerate(accounts)
    ]
    tree_hash = _merkle_tree_hash(leaf_hashes)
    ledger_anchor = _snapshot_ledger_anchor(snapshot)
    root = {
        "schema": LEDGER_SNAPSHOT_MERKLE_ROOT_SCHEMA,
        "schema_version": LEDGER_SNAPSHOT_MERKLE_ROOT_SCHEMA_VERSION,
        "snapshot_schema": snapshot.get("schema"),
        "hash_algorithm": MERKLE_HASH_ALGORITHM,
        "leaf_schema": LEDGER_SNAPSHOT_ACCOUNT_LEAF_SCHEMA,
        "leaf_count": len(accounts),
        "ledger_anchor": ledger_anchor,
        "tree_hash": tree_hash,
    }
    root["root_hash"] = _root_hash(root)
    return root


def ledger_snapshot_account_proof(snapshot: dict[str, Any], account: str) -> dict[str, Any] | None:
    """Return a proof for one account row, or None when the account is absent."""
    accounts = _snapshot_accounts(snapshot)
    index_by_account = {str(row["account"]): index for index, row in enumerate(accounts)}
    if account not in index_by_account:
        return None
    leaf_index = index_by_account[account]
    leaves = [_account_leaf(row, index) for index, row in enumerate(accounts)]
    leaf_hashes = [_account_leaf_hash(leaf) for leaf in leaves]
    return {
        "schema": LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA,
        "schema_version": LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA_VERSION,
        "root": ledger_snapshot_merkle_root(snapshot),
        "tree_size": len(leaves),
        "leaf_index": leaf_index,
        "leaf": leaves[leaf_index],
        "siblings": _proof_siblings(leaf_hashes, leaf_index),
    }


def verify_ledger_snapshot_account_proof(proof: dict[str, Any]) -> bool:
    """Verify a generated account proof against its embedded root object."""
    try:
        if proof.get("schema") != LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA:
            return False
        if proof.get("schema_version") != LEDGER_SNAPSHOT_ACCOUNT_PROOF_SCHEMA_VERSION:
            return False
        root = cast(dict[str, Any], proof["root"])
        leaf = cast(dict[str, Any], proof["leaf"])
        leaf_index = proof["leaf_index"]
        tree_size = proof["tree_size"]
        siblings = cast(list[dict[str, Any]], proof["siblings"])
    except (KeyError, TypeError):
        return False

    if not _valid_root_shape(root):
        return False
    if not _valid_leaf_shape(leaf):
        return False
    if not isinstance(leaf_index, int) or not isinstance(tree_size, int):
        return False
    if tree_size <= 0 or leaf_index < 0 or leaf_index >= tree_size:
        return False
    if leaf.get("leaf_index") != leaf_index:
        return False
    if root.get("leaf_count") != tree_size:
        return False
    if not isinstance(siblings, list):
        return False

    current_hash = _account_leaf_hash(leaf)
    for sibling in siblings:
        if not isinstance(sibling, dict):
            return False
        direction = sibling.get("direction")
        sibling_hash = sibling.get("hash")
        if direction not in {"left", "right"} or not _is_hash_hex(sibling_hash):
            return False
        if direction == "left":
            current_hash = _node_hash(str(sibling_hash), current_hash)
        else:
            current_hash = _node_hash(current_hash, str(sibling_hash))

    if current_hash != root.get("tree_hash"):
        return False
    return _root_hash(root) == root.get("root_hash")


def ledger_snapshot_merkle_root_json(root: dict[str, Any]) -> str:
    return canonical_json(root) + "\n"


def ledger_snapshot_account_proof_json(proof: dict[str, Any]) -> str:
    return canonical_json(proof) + "\n"


def _snapshot_accounts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    accounts = snapshot.get("accounts", [])
    if not isinstance(accounts, list):
        raise ValueError("snapshot accounts must be a list")
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(accounts):
        if not isinstance(row, dict):
            raise ValueError("snapshot account rows must be objects")
        account = row.get("account")
        balance = row.get("balance_microunits")
        if not isinstance(account, str) or not isinstance(balance, int):
            raise ValueError("snapshot account rows require account and integer balance")
        normalized.append({"account": account, "balance_microunits": balance})
        if index > 0 and normalized[index - 1]["account"] > account:
            raise ValueError("snapshot account rows must be sorted by account")
    return normalized


def _snapshot_ledger_anchor(snapshot: dict[str, Any]) -> dict[str, Any]:
    anchor = snapshot.get("ledger_anchor")
    if not isinstance(anchor, dict):
        raise ValueError("snapshot ledger_anchor must be an object")
    latest_sequence = anchor.get("latest_sequence")
    latest_entry_hash = anchor.get("latest_entry_hash")
    if not isinstance(latest_sequence, int):
        raise ValueError("snapshot ledger_anchor.latest_sequence must be an integer")
    if latest_entry_hash is not None and not _is_hash_hex(latest_entry_hash):
        raise ValueError("snapshot ledger_anchor.latest_entry_hash must be a hash or null")
    return {
        "latest_sequence": latest_sequence,
        "latest_entry_hash": latest_entry_hash,
    }


def _account_leaf(row: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "schema": LEDGER_SNAPSHOT_ACCOUNT_LEAF_SCHEMA,
        "schema_version": LEDGER_SNAPSHOT_ACCOUNT_LEAF_SCHEMA_VERSION,
        "leaf_index": index,
        "account": row["account"],
        "balance_microunits": row["balance_microunits"],
    }


def _account_leaf_hash(leaf: dict[str, Any]) -> str:
    return _hash_object(_LEAF_HASH_DOMAIN, {"leaf": leaf})


def _node_hash(left_hash: str, right_hash: str) -> str:
    return _hash_object(_NODE_HASH_DOMAIN, {"left": left_hash, "right": right_hash})


def _merkle_tree_hash(leaf_hashes: list[str]) -> str:
    if not leaf_hashes:
        return _hash_object(_EMPTY_TREE_DOMAIN, {"leaf_count": 0})
    level = leaf_hashes
    while len(level) > 1:
        next_level: list[str] = []
        for index in range(0, len(level), 2):
            left_hash = level[index]
            if index + 1 >= len(level):
                next_level.append(left_hash)
            else:
                next_level.append(_node_hash(left_hash, level[index + 1]))
        level = next_level
    return level[0]


def _proof_siblings(leaf_hashes: list[str], leaf_index: int) -> list[dict[str, str]]:
    siblings: list[dict[str, str]] = []
    index = leaf_index
    level = leaf_hashes
    while len(level) > 1:
        if index % 2 == 0:
            sibling_index = index + 1
            if sibling_index < len(level):
                siblings.append({"direction": "right", "hash": level[sibling_index]})
        else:
            sibling_index = index - 1
            siblings.append({"direction": "left", "hash": level[sibling_index]})
        index //= 2
        level = [
            level[pair_index]
            if pair_index + 1 >= len(level)
            else _node_hash(level[pair_index], level[pair_index + 1])
            for pair_index in range(0, len(level), 2)
        ]
    return siblings


def _root_hash(root: dict[str, Any]) -> str:
    return _hash_object(
        _ROOT_HASH_DOMAIN,
        {
            "snapshot_schema": root.get("snapshot_schema"),
            "hash_algorithm": root.get("hash_algorithm"),
            "leaf_schema": root.get("leaf_schema"),
            "leaf_count": root.get("leaf_count"),
            "ledger_anchor": root.get("ledger_anchor"),
            "tree_hash": root.get("tree_hash"),
        },
    )


def _valid_root_shape(root: dict[str, Any]) -> bool:
    return (
        root.get("schema") == LEDGER_SNAPSHOT_MERKLE_ROOT_SCHEMA
        and root.get("schema_version") == LEDGER_SNAPSHOT_MERKLE_ROOT_SCHEMA_VERSION
        and root.get("hash_algorithm") == MERKLE_HASH_ALGORITHM
        and root.get("leaf_schema") == LEDGER_SNAPSHOT_ACCOUNT_LEAF_SCHEMA
        and isinstance(root.get("leaf_count"), int)
        and isinstance(root.get("ledger_anchor"), dict)
        and _is_hash_hex(root.get("tree_hash"))
        and _is_hash_hex(root.get("root_hash"))
    )


def _valid_leaf_shape(leaf: dict[str, Any]) -> bool:
    return (
        leaf.get("schema") == LEDGER_SNAPSHOT_ACCOUNT_LEAF_SCHEMA
        and leaf.get("schema_version") == LEDGER_SNAPSHOT_ACCOUNT_LEAF_SCHEMA_VERSION
        and isinstance(leaf.get("leaf_index"), int)
        and isinstance(leaf.get("account"), str)
        and isinstance(leaf.get("balance_microunits"), int)
    )


def _is_hash_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HASH_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _hash_object(domain: str, payload: dict[str, Any]) -> str:
    envelope = {"domain": domain, "payload": payload}
    return hashlib.sha256(canonical_json(envelope).encode()).hexdigest()
