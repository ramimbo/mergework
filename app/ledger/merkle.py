from __future__ import annotations

import hashlib
from typing import Any

from app.ledger.service import canonical_json

MERKLE_ROOT_SCHEMA = "mergework.ledger_snapshot_merkle_root.v1"
MERKLE_ROOT_SCHEMA_VERSION = 1
MERKLE_ACCOUNT_LEAF_SCHEMA = "mergework.ledger_snapshot_account_leaf.v1"
MERKLE_ACCOUNT_PROOF_SCHEMA = "mergework.ledger_snapshot_account_proof.v1"
MERKLE_ACCOUNT_PROOF_SCHEMA_VERSION = 1
MERKLE_HASH_ALGORITHM = "sha256"

_EMPTY_DOMAIN = "mergework.ledger_snapshot_merkle_empty.v1"
_LEAF_DOMAIN = "mergework.ledger_snapshot_merkle_leaf.v1"
_NODE_DOMAIN = "mergework.ledger_snapshot_merkle_node.v1"
_ROOT_DOMAIN = "mergework.ledger_snapshot_merkle_root.v1"


class MerkleProofError(ValueError):
    pass


def ledger_snapshot_merkle_root(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic Merkle root object for a Phase 2A snapshot."""
    ledger_anchor = _snapshot_ledger_anchor(snapshot)
    leaves = _snapshot_account_leaves(snapshot)
    merkle_tree_hash = _merkle_tree_hash([_leaf_hash(leaf) for leaf in leaves])
    return {
        "schema": MERKLE_ROOT_SCHEMA,
        "schema_version": MERKLE_ROOT_SCHEMA_VERSION,
        "hash_algorithm": MERKLE_HASH_ALGORITHM,
        "leaf_schema": MERKLE_ACCOUNT_LEAF_SCHEMA,
        "ledger_anchor": ledger_anchor,
        "tree_size": len(leaves),
        "merkle_tree_hash": merkle_tree_hash,
        "root_hash": _root_hash(ledger_anchor, len(leaves), merkle_tree_hash),
    }


def ledger_snapshot_account_proof(snapshot: dict[str, Any], account: str) -> dict[str, Any]:
    """Return an account-balance membership proof for a Phase 2A snapshot."""
    if not isinstance(account, str) or not account:
        raise MerkleProofError("account must be a non-empty string")

    leaves = _snapshot_account_leaves(snapshot)
    try:
        leaf_index = next(index for index, leaf in enumerate(leaves) if leaf["account"] == account)
    except StopIteration as exc:
        raise MerkleProofError("account is not present in the snapshot") from exc

    leaf = leaves[leaf_index]
    leaf_hashes = [_leaf_hash(candidate) for candidate in leaves]
    return {
        "schema": MERKLE_ACCOUNT_PROOF_SCHEMA,
        "schema_version": MERKLE_ACCOUNT_PROOF_SCHEMA_VERSION,
        "hash_algorithm": MERKLE_HASH_ALGORITHM,
        "root": ledger_snapshot_merkle_root(snapshot),
        "leaf": leaf,
        "leaf_index": leaf_index,
        "leaf_hash": leaf_hashes[leaf_index],
        "proof": _proof_path(leaf_hashes, leaf_index),
    }


def verify_ledger_snapshot_account_proof(
    proof: dict[str, Any],
    *,
    root: dict[str, Any] | None = None,
) -> bool:
    """Verify a Phase 2B account proof against its root object."""
    try:
        _validate_proof_shape(proof)
        proof_root = proof["root"]
        if root is not None and proof_root != root:
            return False
        if proof["leaf_hash"] != _leaf_hash(proof["leaf"]):
            return False
        if proof_root["tree_size"] <= proof["leaf_index"]:
            return False

        leaf_hash = proof["leaf_hash"]
        if not isinstance(leaf_hash, str):
            return False
        current_hash = leaf_hash
        index = proof["leaf_index"]
        layer_size = proof_root["tree_size"]
        for sibling in proof["proof"]:
            while layer_size > 1 and index % 2 == 0 and index + 1 >= layer_size:
                index //= 2
                layer_size = (layer_size + 1) // 2
            if layer_size <= 1:
                return False
            position = sibling["position"]
            sibling_hash = sibling["hash"]
            if not isinstance(position, str) or not isinstance(sibling_hash, str):
                return False
            if position == "left":
                if index % 2 == 0:
                    return False
                current_hash = _node_hash(sibling_hash, current_hash)
            elif position == "right":
                if index % 2 != 0 or index + 1 >= layer_size:
                    return False
                current_hash = _node_hash(current_hash, sibling_hash)
            else:
                return False
            index //= 2
            layer_size = (layer_size + 1) // 2

        while layer_size > 1 and index % 2 == 0 and index + 1 >= layer_size:
            index //= 2
            layer_size = (layer_size + 1) // 2

        merkle_tree_hash = proof_root["merkle_tree_hash"]
        root_hash = proof_root["root_hash"]
        if not isinstance(merkle_tree_hash, str) or not isinstance(root_hash, str):
            return False
        expected_root_hash = _root_hash(
            proof_root["ledger_anchor"],
            proof_root["tree_size"],
            merkle_tree_hash,
        )
        return (
            current_hash == merkle_tree_hash
            and root_hash == expected_root_hash
            and index == 0
            and layer_size == 1
        )
    except (KeyError, TypeError, MerkleProofError):
        return False


def ledger_snapshot_merkle_root_json(snapshot: dict[str, Any]) -> str:
    return canonical_json(ledger_snapshot_merkle_root(snapshot)) + "\n"


def ledger_snapshot_account_proof_json(snapshot: dict[str, Any], account: str) -> str:
    return canonical_json(ledger_snapshot_account_proof(snapshot, account)) + "\n"


def _snapshot_ledger_anchor(snapshot: dict[str, Any]) -> dict[str, Any]:
    try:
        anchor = snapshot["ledger_anchor"]
        latest_sequence = anchor["latest_sequence"]
        latest_entry_hash = anchor["latest_entry_hash"]
    except KeyError as exc:
        raise MerkleProofError("snapshot must include a ledger anchor") from exc
    if isinstance(latest_sequence, bool) or not isinstance(latest_sequence, int):
        raise MerkleProofError("latest_sequence must be an integer")
    if latest_entry_hash is not None and not _is_hash_hex(latest_entry_hash):
        raise MerkleProofError("latest_entry_hash must be a SHA-256 hash or null")
    return {
        "latest_sequence": latest_sequence,
        "latest_entry_hash": latest_entry_hash,
    }


def _snapshot_account_leaves(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    accounts = snapshot.get("accounts")
    if not isinstance(accounts, list):
        raise MerkleProofError("snapshot accounts must be a list")

    leaves: list[dict[str, Any]] = []
    seen_accounts: set[str] = set()
    for row in accounts:
        if not isinstance(row, dict):
            raise MerkleProofError("snapshot account rows must be objects")
        account = row.get("account")
        balance = row.get("balance_microunits")
        if not isinstance(account, str) or not account:
            raise MerkleProofError("account must be a non-empty string")
        if account in seen_accounts:
            raise MerkleProofError("snapshot accounts must be unique")
        if isinstance(balance, bool) or not isinstance(balance, int):
            raise MerkleProofError("balance_microunits must be an integer")
        seen_accounts.add(account)
        leaves.append(
            {
                "schema": MERKLE_ACCOUNT_LEAF_SCHEMA,
                "account": account,
                "balance_microunits": balance,
            }
        )
    return sorted(leaves, key=lambda leaf: leaf["account"])


def _merkle_tree_hash(leaf_hashes: list[str]) -> str:
    if not leaf_hashes:
        return _hash_json(
            _EMPTY_DOMAIN,
            {
                "schema": MERKLE_ROOT_SCHEMA,
                "tree_size": 0,
            },
        )
    layer = leaf_hashes
    while len(layer) > 1:
        layer = _next_layer(layer)
    return layer[0]


def _proof_path(leaf_hashes: list[str], leaf_index: int) -> list[dict[str, str]]:
    proof: list[dict[str, str]] = []
    index = leaf_index
    layer = leaf_hashes
    while len(layer) > 1:
        if index % 2 == 0:
            sibling_index = index + 1
            if sibling_index < len(layer):
                proof.append({"position": "right", "hash": layer[sibling_index]})
        else:
            proof.append({"position": "left", "hash": layer[index - 1]})
        index //= 2
        layer = _next_layer(layer)
    return proof


def _next_layer(layer: list[str]) -> list[str]:
    next_layer: list[str] = []
    for index in range(0, len(layer), 2):
        if index + 1 >= len(layer):
            next_layer.append(layer[index])
        else:
            next_layer.append(_node_hash(layer[index], layer[index + 1]))
    return next_layer


def _leaf_hash(leaf: dict[str, Any]) -> str:
    if leaf.get("schema") != MERKLE_ACCOUNT_LEAF_SCHEMA:
        raise MerkleProofError("unsupported account leaf schema")
    account = leaf.get("account")
    balance = leaf.get("balance_microunits")
    if not isinstance(account, str) or not account:
        raise MerkleProofError("account must be a non-empty string")
    if isinstance(balance, bool) or not isinstance(balance, int):
        raise MerkleProofError("balance_microunits must be an integer")
    return _hash_json(
        _LEAF_DOMAIN,
        {
            "schema": MERKLE_ACCOUNT_LEAF_SCHEMA,
            "account": account,
            "balance_microunits": balance,
        },
    )


def _node_hash(left_hash: str, right_hash: str) -> str:
    if not _is_hash_hex(left_hash) or not _is_hash_hex(right_hash):
        raise MerkleProofError("Merkle node children must be SHA-256 hashes")
    return _hash_json(
        _NODE_DOMAIN,
        {
            "left": left_hash,
            "right": right_hash,
        },
    )


def _root_hash(ledger_anchor: dict[str, Any], tree_size: int, merkle_tree_hash: str) -> str:
    if isinstance(tree_size, bool) or not isinstance(tree_size, int):
        raise MerkleProofError("tree_size must be an integer")
    if tree_size < 0:
        raise MerkleProofError("tree_size must be non-negative")
    if not _is_hash_hex(merkle_tree_hash):
        raise MerkleProofError("merkle_tree_hash must be a SHA-256 hash")
    return _hash_json(
        _ROOT_DOMAIN,
        {
            "ledger_anchor": _snapshot_ledger_anchor({"ledger_anchor": ledger_anchor}),
            "merkle_tree_hash": merkle_tree_hash,
            "tree_size": tree_size,
        },
    )


def _hash_json(domain: str, payload: dict[str, Any]) -> str:
    body = canonical_json(
        {
            "domain": domain,
            "payload": payload,
        }
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _validate_proof_shape(proof: dict[str, Any]) -> None:
    if proof.get("schema") != MERKLE_ACCOUNT_PROOF_SCHEMA:
        raise MerkleProofError("unsupported proof schema")
    if proof.get("schema_version") != MERKLE_ACCOUNT_PROOF_SCHEMA_VERSION:
        raise MerkleProofError("unsupported proof schema version")
    if proof.get("hash_algorithm") != MERKLE_HASH_ALGORITHM:
        raise MerkleProofError("unsupported proof hash algorithm")
    if not isinstance(proof.get("leaf_index"), int) or isinstance(proof.get("leaf_index"), bool):
        raise MerkleProofError("leaf_index must be an integer")
    if proof["leaf_index"] < 0:
        raise MerkleProofError("leaf_index must be non-negative")
    if not _is_hash_hex(proof.get("leaf_hash")):
        raise MerkleProofError("leaf_hash must be a SHA-256 hash")
    if not isinstance(proof.get("proof"), list):
        raise MerkleProofError("proof path must be a list")
    _validate_root_shape(proof.get("root"))
    for sibling in proof["proof"]:
        if not isinstance(sibling, dict):
            raise MerkleProofError("proof sibling must be an object")
        if sibling.get("position") not in {"left", "right"}:
            raise MerkleProofError("proof sibling position is invalid")
        if not _is_hash_hex(sibling.get("hash")):
            raise MerkleProofError("proof sibling hash must be a SHA-256 hash")


def _validate_root_shape(root: Any) -> None:
    if not isinstance(root, dict):
        raise MerkleProofError("root must be an object")
    if root.get("schema") != MERKLE_ROOT_SCHEMA:
        raise MerkleProofError("unsupported root schema")
    if root.get("schema_version") != MERKLE_ROOT_SCHEMA_VERSION:
        raise MerkleProofError("unsupported root schema version")
    if root.get("hash_algorithm") != MERKLE_HASH_ALGORITHM:
        raise MerkleProofError("unsupported root hash algorithm")
    if root.get("leaf_schema") != MERKLE_ACCOUNT_LEAF_SCHEMA:
        raise MerkleProofError("unsupported root leaf schema")
    if not isinstance(root.get("tree_size"), int) or isinstance(root.get("tree_size"), bool):
        raise MerkleProofError("tree_size must be an integer")
    if root["tree_size"] <= 0:
        raise MerkleProofError("account proofs require a non-empty tree")
    if not _is_hash_hex(root.get("merkle_tree_hash")):
        raise MerkleProofError("merkle_tree_hash must be a SHA-256 hash")
    if not _is_hash_hex(root.get("root_hash")):
        raise MerkleProofError("root_hash must be a SHA-256 hash")
    anchor = root.get("ledger_anchor")
    if not isinstance(anchor, dict):
        raise MerkleProofError("root ledger anchor must be an object")
    _snapshot_ledger_anchor({"ledger_anchor": anchor})


def _is_hash_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )
