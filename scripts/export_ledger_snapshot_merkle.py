from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.ledger.merkle import (
    SnapshotMerkleError,
    snapshot_account_proof,
    snapshot_account_proof_json,
    snapshot_merkle_root,
    snapshot_merkle_root_json,
)
from app.ledger.snapshot import ledger_snapshot
from scripts.export_ledger_snapshot import read_only_session_scope


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export deterministic Merkle artifacts for an MRWK ledger snapshot."
    )
    parser.add_argument("--database-url", help="Database URL. Defaults to MERGEWORK_DATABASE_URL.")
    parser.add_argument("--source-host", help="Public source host/origin for snapshot metadata.")
    parser.add_argument(
        "--source-mode",
        default="database",
        help="Source mode label for snapshot metadata. Defaults to database.",
    )
    parser.add_argument(
        "--account",
        help="Print a Merkle proof for this account instead of the snapshot Merkle root.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    database_url = args.database_url or settings.database_url
    source_host = args.source_host if args.source_host is not None else settings.public_base_url
    with read_only_session_scope(database_url) as session:
        snapshot = ledger_snapshot(
            session,
            source_mode=args.source_mode,
            source_host=source_host,
        )
        if args.account:
            try:
                sys.stdout.write(
                    snapshot_account_proof_json(snapshot_account_proof(snapshot, args.account))
                )
            except SnapshotMerkleError as exc:
                parser.error(str(exc))
        else:
            sys.stdout.write(snapshot_merkle_root_json(snapshot_merkle_root(snapshot)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
