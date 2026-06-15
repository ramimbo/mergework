from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.db import create_schema, session_scope
from app.ledger.service import TREASURY_ACCOUNT, add_ledger_entry, ensure_genesis, register_wallet
from app.me import me_page_context
from app.wallets import address_from_public_key_hex


def _wallet_public_hex() -> str:
    private_key = Ed25519PrivateKey.generate()
    return (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        .hex()
    )


def test_me_page_context_defaults_for_anonymous_user(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

        context = me_page_context(session, None)

    assert context == {
        "github_login": None,
        "github_balance_mrwk": "0",
        "linked_wallet_address": "",
        "github_account_url": "",
        "linked_wallet_url": "",
    }


def test_me_page_context_reports_balance_and_linked_wallet(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    public_hex = _wallet_public_hex()
    address = address_from_public_key_hex(public_hex)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        add_ledger_entry(
            session,
            entry_type="test_github_balance",
            from_account=TREASURY_ACCOUNT,
            to_account="github:alice",
            amount_microunits=4_000_000,
            reference="test-github-balance",
        )
        register_wallet(session, public_key_hex=public_hex, github_login="alice")

        context = me_page_context(session, "alice")

    assert context == {
        "github_login": "alice",
        "github_balance_mrwk": "4",
        "linked_wallet_address": address,
        "github_account_url": "/accounts/github:alice",
        "linked_wallet_url": f"/wallets/{address}",
    }
