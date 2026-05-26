from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.db import create_schema, session_scope
from app.ledger.service import TREASURY_ACCOUNT, add_ledger_entry, ensure_genesis, register_wallet
from app.models import Proof
from app.wallet_views import wallet_address_from_path, wallet_detail_context, wallet_list_context
from app.wallets import address_from_public_key_hex


def test_wallet_list_context_serializes_registered_wallet(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    public_key_hex = "11" * 32
    address = address_from_public_key_hex(public_key_hex)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        register_wallet(session, public_key_hex=public_key_hex, label="Main wallet")

    context = wallet_list_context(sqlite_url)

    assert context["wallets"][0]["address"] == address
    assert context["wallets"][0]["label"] == "Main wallet"
    assert context["wallets"][0]["balance_mrwk"] == "0"


def test_wallet_detail_context_includes_transactions_and_proof_hash(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    public_key_hex = "22" * 32
    address = address_from_public_key_hex(public_key_hex)
    proof_hash = "a" * 64
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        register_wallet(session, public_key_hex=public_key_hex, label="Funded wallet")
        entry = add_ledger_entry(
            session,
            entry_type="test_funding",
            from_account=TREASURY_ACCOUNT,
            to_account=address,
            amount_microunits=2_500_000,
            reference="test funding",
        )
        session.add(
            Proof(
                hash=proof_hash,
                ledger_sequence=entry.sequence,
                bounty_id=None,
                submission_id=None,
                kind="test_proof",
                public_json="{}",
            )
        )

    context = wallet_detail_context(sqlite_url, address.upper())

    assert context["wallet"]["address"] == address
    assert context["wallet"]["balance_mrwk"] == "2.5"
    assert context["transactions"][0]["sequence"] == entry.sequence
    assert context["transactions"][0]["proof_hash"] == proof_hash


def test_wallet_detail_context_reports_invalid_or_missing_wallet(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with pytest.raises(HTTPException) as invalid:
        wallet_address_from_path("not-a-wallet")
    assert invalid.value.status_code == 400
    assert invalid.value.detail == "invalid MRWK wallet address"

    missing_address = "mrwk1" + ("0" * 40)
    with pytest.raises(HTTPException) as missing:
        wallet_detail_context(sqlite_url, missing_address)
    assert missing.value.status_code == 404
    assert missing.value.detail == "wallet not found"
