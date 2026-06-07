from __future__ import annotations

import hashlib
from typing import Any

from app.ledger.service import canonical_json
from app.ledger.snapshot import LEDGER_SNAPSHOT_SCHEMA, LEDGER_SNAPSHOT_SCHEMA_VERSION

HASH_ALGORITHM = "sha256"
SNAPSHOT_MERKLE_ROOT_SCHEMA = "mergework.ledger_snapshot_merkle_root.v1"
SNAPSHOT_MERKLE_PROOF_SCHEMA = "mergework.ledger_snapshot_merkle_proof.v1"
SNAPSHOT_MERKLE_ACCOUNT_LEAF_SCHEMA = "mergework.ledger_snapshot_account_leaf.v1"
SNAPSHOT_MERKLE_NODE_SCHEMA = "mergework.ledger_snapshot_merkle_node.v1"
SNAPSHOT_MERKLE_EMPTY_TREE_SCHEMA = "mergework.ledger_snapshot_merkle_empty_tree.v1"
SNAPSHOT_MERKLE_ROOT_HASH_INPUT_SCHEMA = "mergework.ledger_snapshot_merkle_root_input.v1"
SNAPSHOT_MERKLE_SCHEMA_VERSION = 1


class SnapshotMerkleError(ValueError):
    pass


def snapshot_merkle_root(snapshot: dict[str, Any]) -> dict[str, Any]:
    accounts = _snapshot_accounts(snapshot)
    return _root_object(snapshot, _account_tree_hash(accounts), len(accounts))


def snapshot_account_proof(snapshot: dict[str, Any], account: str) -> dict[str, Any]:
    accounts = _snapshot_accounts(snapshot)
    leaves = [_account_leaf(row) for row in accounts]
    leaf_hashes = [_hash_object(leaf) for leaf in leaves]
    leaf_index = next(
        (index for index, leaf in enumerate(leaves) if leaf["account"] == account),
        None,
    )
    if leaf_index is None:
        raise SnapshotMerkleError("account not found in snapshot")

    levels = _merkle_levels(leaf_hashes)
    return {
        "schema": SNAPSHOT_MERKLE_PROOF_SCHEMA,
        "schema_version": SNAPSHOT_MERKLE_SCHEMA_VERSION,
        "root": _root_object(snapshot, levels[-1][0], len(leaves)),
        "leaf": leaves[leaf_index],
        "leaf_index": leaf_index,
        "tree_size": len(leaves),
        "sibling_path": _sibling_path(levels, leaf_index),
    }


def snapshot_merkle_root_json(root: dict[str, Any]) -> str:
    return canonical_json(root) + "\n"


def snapshot_account_proof_json(proof: dict[str, Any]) -> str:
    return canonical_json(proof) + "\n"


def verify_snapshot_account_proof(
    proof: object,
    expected_root: dict[str, Any] | None = None,
) -> bool:
    try:
        if not isinstance(proof, dict):
            return False
        if _str_field(proof.get("schema"), "proof.schema") != SNAPSHOT_MERKLE_PROOF_SCHEMA:
            return False
        if (
            _int_field(proof.get("schema_version"), "proof.schema_version")
            != SNAPSHOT_MERKLE_SCHEMA_VERSION
        ):
            return False

        root = _validated_root_object(proof.get("root"))
        if expected_root is not None and root != _validated_root_object(expected_root):
            return False

        leaf = _validated_leaf_object(proof.get("leaf"))
        leaf_index = _int_field(proof.get("leaf_index"), "leaf_index", minimum=0)
        tree_size = _int_field(proof.get("tree_size"), "tree_size", minimum=1)
        if leaf_index >= tree_size or root["tree_size"] != tree_size:
            return False

        sibling_path = _validated_sibling_path(proof.get("sibling_path"))
        if [step["direction"] for step in sibling_path] != _expected_sibling_directions(
            leaf_index,
            tree_size,
        ):
            return False

        account_tree_hash = _hash_object(leaf)
        for step in sibling_path:
            if step["direction"] == "right":
                account_tree_hash = _node_hash(account_tree_hash, step["hash"])
            else:
                account_tree_hash = _node_hash(step["hash"], account_tree_hash)
        return account_tree_hash == _hash_field(root["account_tree_hash"], "root.account_tree_hash")
    except (SnapshotMerkleError, TypeError, ValueError):
        return False


def _snapshot_accounts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    if snapshot.get("schema") != LEDGER_SNAPSHOT_SCHEMA:
        raise SnapshotMerkleError("unsupported snapshot schema")
    if snapshot.get("schema_version") != LEDGER_SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotMerkleError("unsupported snapshot schema version")

    accounts = snapshot.get("accounts")
    if not isinstance(accounts, list):
        raise SnapshotMerkleError("snapshot accounts must be a list")

    normalized: list[dict[str, Any]] = []
    seen_accounts: set[str] = set()
    for row in accounts:
        if not isinstance(row, dict):
            raise SnapshotMerkleError("snapshot account row must be an object")
        account = _str_field(row.get("account"), "account")
        balance_microunits = _int_field(row.get("balance_microunits"), "balance_microunits")
        if account in seen_accounts:
            raise SnapshotMerkleError("duplicate snapshot account")
        seen_accounts.add(account)
        normalized.append(
            {
                "account": account,
                "balance_microunits": balance_microunits,
            }
        )
    return sorted(normalized, key=lambda row: str(row["account"]))


def _root_object(
    snapshot: dict[str, Any],
    account_tree_hash: str,
    tree_size: int,
) -> dict[str, Any]:
    snapshot_schema = _str_field(snapshot.get("schema"), "schema")
    snapshot_schema_version = _int_field(snapshot.get("schema_version"), "schema_version")
    ledger_anchor = _ledger_anchor(snapshot.get("ledger_anchor"))
    root_hash = _snapshot_root_hash(
        snapshot_schema=snapshot_schema,
        snapshot_schema_version=snapshot_schema_version,
        ledger_anchor=ledger_anchor,
        tree_size=tree_size,
        account_tree_hash=account_tree_hash,
    )
    return {
        "schema": SNAPSHOT_MERKLE_ROOT_SCHEMA,
        "schema_version": SNAPSHOT_MERKLE_SCHEMA_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "snapshot_schema": snapshot_schema,
        "snapshot_schema_version": snapshot_schema_version,
        "ledger_anchor": ledger_anchor,
        "tree_size": tree_size,
        "account_tree_hash": account_tree_hash,
        "root_hash": root_hash,
        "leaf_schema": SNAPSHOT_MERKLE_ACCOUNT_LEAF_SCHEMA,
        "node_schema": SNAPSHOT_MERKLE_NODE_SCHEMA,
        "empty_tree_schema": SNAPSHOT_MERKLE_EMPTY_TREE_SCHEMA,
        "root_hash_input_schema": SNAPSHOT_MERKLE_ROOT_HASH_INPUT_SCHEMA,
    }


def _account_leaf(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SNAPSHOT_MERKLE_ACCOUNT_LEAF_SCHEMA,
        "schema_version": SNAPSHOT_MERKLE_SCHEMA_VERSION,
        "account": _str_field(row.get("account"), "account"),
        "balance_microunits": _int_field(row.get("balance_microunits"), "balance_microunits"),
    }


def _account_tree_hash(accounts: list[dict[str, Any]]) -> str:
    if not accounts:
        return _hash_object(
            {
                "schema": SNAPSHOT_MERKLE_EMPTY_TREE_SCHEMA,
                "schema_version": SNAPSHOT_MERKLE_SCHEMA_VERSION,
            }
        )
    levels = _merkle_levels([_hash_object(_account_leaf(row)) for row in accounts])
    return levels[-1][0]


def _merkle_levels(leaf_hashes: list[str]) -> list[list[str]]:
    levels = [leaf_hashes]
    current_level = leaf_hashes
    while len(current_level) > 1:
        next_level: list[str] = []
        for index in range(0, len(current_level), 2):
            left = current_level[index]
            if index + 1 >= len(current_level):
                next_level.append(left)
            else:
                next_level.append(_node_hash(left, current_level[index + 1]))
        levels.append(next_level)
        current_level = next_level
    return levels


def _sibling_path(levels: list[list[str]], leaf_index: int) -> list[dict[str, str]]:
    path: list[dict[str, str]] = []
    index = leaf_index
    for level in levels[:-1]:
        if index % 2 == 0:
            sibling_index = index + 1
            if sibling_index < len(level):
                path.append({"direction": "right", "hash": level[sibling_index]})
        else:
            path.append({"direction": "left", "hash": level[index - 1]})
        index //= 2
    return path


def _expected_sibling_directions(leaf_index: int, tree_size: int) -> list[str]:
    directions: list[str] = []
    index = leaf_index
    level_size = tree_size
    while level_size > 1:
        if index % 2 == 0:
            if index + 1 < level_size:
                directions.append("right")
        else:
            directions.append("left")
        index //= 2
        level_size = (level_size + 1) // 2
    return directions


def _node_hash(left_hash: str, right_hash: str) -> str:
    return _hash_object(
        {
            "schema": SNAPSHOT_MERKLE_NODE_SCHEMA,
            "schema_version": SNAPSHOT_MERKLE_SCHEMA_VERSION,
            "left": left_hash,
            "right": right_hash,
        }
    )


def _snapshot_root_hash(
    *,
    snapshot_schema: str,
    snapshot_schema_version: int,
    ledger_anchor: dict[str, Any],
    tree_size: int,
    account_tree_hash: str,
) -> str:
    return _hash_object(
        {
            "schema": SNAPSHOT_MERKLE_ROOT_HASH_INPUT_SCHEMA,
            "schema_version": SNAPSHOT_MERKLE_SCHEMA_VERSION,
            "hash_algorithm": HASH_ALGORITHM,
            "snapshot_schema": snapshot_schema,
            "snapshot_schema_version": snapshot_schema_version,
            "ledger_anchor": ledger_anchor,
            "tree_size": tree_size,
            "account_tree_hash": account_tree_hash,
        }
    )


def _validated_root_object(raw_root: Any) -> dict[str, Any]:
    if not isinstance(raw_root, dict):
        raise SnapshotMerkleError("root must be an object")

    root: dict[str, Any] = {
        "schema": _str_field(raw_root.get("schema"), "root.schema"),
        "schema_version": _int_field(raw_root.get("schema_version"), "root.schema_version"),
        "hash_algorithm": _str_field(raw_root.get("hash_algorithm"), "root.hash_algorithm"),
        "snapshot_schema": _str_field(raw_root.get("snapshot_schema"), "root.snapshot_schema"),
        "snapshot_schema_version": _int_field(
            raw_root.get("snapshot_schema_version"),
            "root.snapshot_schema_version",
        ),
        "ledger_anchor": _ledger_anchor(raw_root.get("ledger_anchor")),
        "tree_size": _int_field(raw_root.get("tree_size"), "root.tree_size", minimum=0),
        "account_tree_hash": _hash_field(
            raw_root.get("account_tree_hash"),
            "root.account_tree_hash",
        ),
        "root_hash": _hash_field(raw_root.get("root_hash"), "root.root_hash"),
        "leaf_schema": _str_field(raw_root.get("leaf_schema"), "root.leaf_schema"),
        "node_schema": _str_field(raw_root.get("node_schema"), "root.node_schema"),
        "empty_tree_schema": _str_field(
            raw_root.get("empty_tree_schema"),
            "root.empty_tree_schema",
        ),
        "root_hash_input_schema": _str_field(
            raw_root.get("root_hash_input_schema"),
            "root.root_hash_input_schema",
        ),
    }
    if root["schema"] != SNAPSHOT_MERKLE_ROOT_SCHEMA:
        raise SnapshotMerkleError("unsupported root schema")
    if root["schema_version"] != SNAPSHOT_MERKLE_SCHEMA_VERSION:
        raise SnapshotMerkleError("unsupported root schema version")
    if root["hash_algorithm"] != HASH_ALGORITHM:
        raise SnapshotMerkleError("unsupported hash algorithm")
    if root["snapshot_schema"] != LEDGER_SNAPSHOT_SCHEMA:
        raise SnapshotMerkleError("unsupported snapshot schema")
    if root["snapshot_schema_version"] != LEDGER_SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotMerkleError("unsupported snapshot schema version")
    if root["leaf_schema"] != SNAPSHOT_MERKLE_ACCOUNT_LEAF_SCHEMA:
        raise SnapshotMerkleError("unsupported leaf schema")
    if root["node_schema"] != SNAPSHOT_MERKLE_NODE_SCHEMA:
        raise SnapshotMerkleError("unsupported node schema")
    if root["empty_tree_schema"] != SNAPSHOT_MERKLE_EMPTY_TREE_SCHEMA:
        raise SnapshotMerkleError("unsupported empty tree schema")
    if root["root_hash_input_schema"] != SNAPSHOT_MERKLE_ROOT_HASH_INPUT_SCHEMA:
        raise SnapshotMerkleError("unsupported root hash input schema")
    if root["root_hash"] != _snapshot_root_hash(
        snapshot_schema=root["snapshot_schema"],
        snapshot_schema_version=root["snapshot_schema_version"],
        ledger_anchor=root["ledger_anchor"],
        tree_size=root["tree_size"],
        account_tree_hash=root["account_tree_hash"],
    ):
        raise SnapshotMerkleError("root hash mismatch")
    return root


def _validated_leaf_object(raw_leaf: Any) -> dict[str, Any]:
    if not isinstance(raw_leaf, dict):
        raise SnapshotMerkleError("leaf must be an object")
    leaf: dict[str, Any] = {
        "schema": _str_field(raw_leaf.get("schema"), "leaf.schema"),
        "schema_version": _int_field(raw_leaf.get("schema_version"), "leaf.schema_version"),
        "account": _str_field(raw_leaf.get("account"), "leaf.account"),
        "balance_microunits": _int_field(
            raw_leaf.get("balance_microunits"),
            "leaf.balance_microunits",
        ),
    }
    if leaf["schema"] != SNAPSHOT_MERKLE_ACCOUNT_LEAF_SCHEMA:
        raise SnapshotMerkleError("unsupported leaf schema")
    if leaf["schema_version"] != SNAPSHOT_MERKLE_SCHEMA_VERSION:
        raise SnapshotMerkleError("unsupported leaf schema version")
    return leaf


def _validated_sibling_path(raw_path: Any) -> list[dict[str, str]]:
    if not isinstance(raw_path, list):
        raise SnapshotMerkleError("sibling_path must be a list")
    path: list[dict[str, str]] = []
    for raw_step in raw_path:
        if not isinstance(raw_step, dict):
            raise SnapshotMerkleError("sibling_path step must be an object")
        direction = _str_field(raw_step.get("direction"), "sibling_path.direction")
        if direction not in {"left", "right"}:
            raise SnapshotMerkleError("invalid sibling direction")
        path.append(
            {
                "direction": direction,
                "hash": _hash_field(raw_step.get("hash"), "sibling_path.hash"),
            }
        )
    return path


def _ledger_anchor(raw_anchor: Any) -> dict[str, Any]:
    if not isinstance(raw_anchor, dict):
        raise SnapshotMerkleError("ledger_anchor must be an object")
    latest_sequence = _int_field(raw_anchor.get("latest_sequence"), "ledger_anchor.latest_sequence")
    latest_entry_hash = raw_anchor.get("latest_entry_hash")
    if latest_entry_hash is not None:
        latest_entry_hash = _hash_field(latest_entry_hash, "ledger_anchor.latest_entry_hash")
    return {
        "latest_sequence": latest_sequence,
        "latest_entry_hash": latest_entry_hash,
    }


def _str_field(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SnapshotMerkleError(f"{field} must be a non-empty string")
    return value


def _int_field(value: Any, field: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SnapshotMerkleError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise SnapshotMerkleError(f"{field} is below minimum")
    return value


def _hash_field(value: Any, field: str) -> str:
    text = _str_field(value, field)
    if len(text) != 64 or text.lower() != text:
        raise SnapshotMerkleError(f"{field} must be a lowercase sha256 hash")
    try:
        int(text, 16)
    except ValueError as exc:
        raise SnapshotMerkleError(f"{field} must be a lowercase sha256 hash") from exc
    return text


def _hash_object(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()
