from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import make_engine
from app.ledger.snapshot import (
    ledger_snapshot,
    ledger_snapshot_account_proof,
    ledger_snapshot_json,
    ledger_snapshot_schema_json,
    verify_ledger_snapshot_account_proof,
)


@contextmanager
def read_only_session_scope(database_url: str) -> Iterator[Session]:
    engine = make_engine(database_url)
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a read-only MRWK ledger snapshot.")
    parser.add_argument("--database-url", help="Database URL. Defaults to MERGEWORK_DATABASE_URL.")
    parser.add_argument("--source-host", help="Public source host/origin for snapshot metadata.")
    parser.add_argument(
        "--source-mode",
        default="database",
        help="Source mode label for snapshot metadata. Defaults to database.",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Print the ledger snapshot JSON Schema instead of a live snapshot.",
    )
    parser.add_argument(
        "--account-proof",
        help="Print a Merkle account proof for this account from the live snapshot.",
    )
    parser.add_argument(
        "--verify-account-proof",
        help="Read a Merkle account proof JSON file and print whether it verifies.",
    )
    parser.add_argument(
        "--expected-root",
        help="Trusted snapshot merkle.root required when verifying an account proof.",
    )
    args = parser.parse_args(argv)
    if args.schema:
        sys.stdout.write(ledger_snapshot_schema_json())
        return 0
    if args.verify_account_proof:
        if not args.expected_root:
            sys.stderr.write("error: --expected-root is required with --verify-account-proof\n")
            return 1
        try:
            with Path(args.verify_account_proof).open(encoding="utf-8") as proof_file:
                proof = json.load(proof_file)
        except (OSError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"error: could not read proof file: {exc}\n")
            return 1
        sys.stdout.write(
            json.dumps(
                {
                    "valid": verify_ledger_snapshot_account_proof(
                        proof, expected_root=args.expected_root
                    ),
                    "expected_root": args.expected_root,
                }
            )
            + "\n"
        )
        return 0

    settings = get_settings()
    database_url = args.database_url or settings.database_url
    source_host = args.source_host if args.source_host is not None else settings.public_base_url
    with read_only_session_scope(database_url) as session:
        snapshot = ledger_snapshot(
            session,
            source_mode=args.source_mode,
            source_host=source_host,
        )
        if args.account_proof:
            try:
                proof_payload = ledger_snapshot_account_proof(snapshot, args.account_proof)
            except ValueError as exc:
                sys.stderr.write(f"error: {exc}\n")
                return 1
            sys.stdout.write(ledger_snapshot_json(proof_payload))
        else:
            sys.stdout.write(ledger_snapshot_json(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
