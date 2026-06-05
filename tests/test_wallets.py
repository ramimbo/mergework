from __future__ import annotations

from collections.abc import Callable

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.db import create_schema, session_scope
from app.ledger.service import (
    TREASURY_ACCOUNT,
    LedgerError,
    add_ledger_entry,
    ensure_genesis,
    get_balance,
    link_wallet_to_github,
    register_wallet,
    submit_github_claim,
    submit_wallet_transfer,
    wallet_claim_payload,
    wallet_link_payload,
    wallet_transfer_payload,
)
from app.models import Wallet
from app.wallets import (
    WalletError,
    address_from_public_key_hex,
    canonical_wallet_json,
    normalize_public_key_hex,
    normalize_signature_hex,
    normalize_wallet_address,
)


def _keypair() -> tuple[Ed25519PrivateKey, str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_hex = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    address = address_from_public_key_hex(public_hex)
    return private_key, public_hex, address


def _sign(private_key: Ed25519PrivateKey, payload: dict[str, object]) -> str:
    return private_key.sign(canonical_wallet_json(payload).encode()).hex()


def test_wallet_address_is_derived_from_public_key() -> None:
    _, public_hex, address = _keypair()

    assert address.startswith("mrwk1")
    assert len(address) == 45
    assert address_from_public_key_hex(public_hex) == address


@pytest.mark.parametrize(
    ("normalizer", "value", "message"),
    [
        (normalize_public_key_hex, "a" * 63 + "\x85", "public key"),
        (normalize_signature_hex, "b" * 127 + "\x85", "signature"),
        (normalize_wallet_address, "mrwk1" + "c" * 39 + "\x85", "MRWK wallet address"),
    ],
)
def test_wallet_normalizers_reject_c1_control_characters(
    normalizer: Callable[[str], str], value: str, message: str
) -> None:
    with pytest.raises(WalletError, match=f"{message} must not contain control characters"):
        normalizer(value)


def test_wallet_registration_rejects_oversized_label(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    _, public_hex, _ = _keypair()

    with (
        session_scope(sqlite_url) as session,
        pytest.raises(LedgerError, match="wallet label is too long"),
    ):
        register_wallet(session, public_key_hex=public_hex, label="x" * 161)


def test_wallet_registration_rejects_label_control_characters(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    _, public_hex, _ = _keypair()

    with (
        session_scope(sqlite_url) as session,
        pytest.raises(LedgerError, match="wallet label must not contain control characters"),
    ):
        register_wallet(session, public_key_hex=public_hex, label="Main\nWallet")


def test_wallet_registration_rejects_non_string_label_and_github_login(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    _, public_hex, _ = _keypair()

    with session_scope(sqlite_url) as session:
        for bad_label in (123, 0, False):
            with pytest.raises(LedgerError, match="wallet label must be a string"):
                register_wallet(session, public_key_hex=public_hex, label=bad_label)
        for bad_login in (123, 0, False):
            with pytest.raises(LedgerError, match="github login must be a string"):
                register_wallet(session, public_key_hex=public_hex, github_login=bad_login)


def test_signed_wallet_transfer_moves_balance_and_rejects_replay(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    sender_key, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        register_wallet(session, public_key_hex=sender_public, label="Sender")
        register_wallet(session, public_key_hex=receiver_public, label="Receiver")
        add_ledger_entry(
            session,
            entry_type="test_funding",
            from_account=TREASURY_ACCOUNT,
            to_account=sender_address,
            amount_microunits=10_000_000,
            reference="test-funding",
        )

        payload = wallet_transfer_payload(
            from_address=sender_address,
            to_address=receiver_address,
            amount_microunits=2_500_000,
            nonce=1,
            memo="first transfer",
        )
        signature = _sign(sender_key, payload)
        transfer = submit_wallet_transfer(
            session,
            from_address=sender_address,
            to_address=receiver_address,
            amount_mrwk="2.5",
            nonce=1,
            memo="first transfer",
            signature_hex=signature,
        )

        assert transfer.hash
        assert transfer.ledger_sequence == 3
        assert get_balance(session, sender_address) == 7_500_000
        assert get_balance(session, receiver_address) == 2_500_000

        with pytest.raises(LedgerError, match="invalid nonce"):
            submit_wallet_transfer(
                session,
                from_address=sender_address,
                to_address=receiver_address,
                amount_mrwk="2.5",
                nonce=1,
                memo="first transfer",
                signature_hex=signature,
            )


def test_wallet_transfer_rejects_memo_control_characters(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    sender_key, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        register_wallet(session, public_key_hex=sender_public)
        register_wallet(session, public_key_hex=receiver_public)
        add_ledger_entry(
            session,
            entry_type="test_funding",
            from_account=TREASURY_ACCOUNT,
            to_account=sender_address,
            amount_microunits=10_000_000,
            reference="test-funding",
        )
        memo = "line1\nline2"
        payload = wallet_transfer_payload(
            from_address=sender_address,
            to_address=receiver_address,
            amount_microunits=2_500_000,
            nonce=1,
            memo=memo,
        )

        with pytest.raises(LedgerError, match="memo must not contain control characters"):
            submit_wallet_transfer(
                session,
                from_address=sender_address,
                to_address=receiver_address,
                amount_mrwk="2.5",
                nonce=1,
                memo=memo,
                signature_hex=_sign(sender_key, payload),
            )


def test_wallet_transfer_rejects_bad_signature(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    sender_key, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        register_wallet(session, public_key_hex=sender_public, label=None)
        register_wallet(session, public_key_hex=receiver_public, label=None)
        add_ledger_entry(
            session,
            entry_type="test_funding",
            from_account=TREASURY_ACCOUNT,
            to_account=sender_address,
            amount_microunits=10_000_000,
            reference="test-funding",
        )

        wrong_payload = wallet_transfer_payload(
            from_address=sender_address,
            to_address=receiver_address,
            amount_microunits=1_000_000,
            nonce=2,
            memo="tampered",
        )
        bad_signature = _sign(sender_key, wrong_payload)

        with pytest.raises(LedgerError, match="invalid signature"):
            submit_wallet_transfer(
                session,
                from_address=sender_address,
                to_address=receiver_address,
                amount_mrwk="1",
                nonce=1,
                memo="tampered",
                signature_hex=bad_signature,
            )


def test_wallet_operations_reject_boolean_nonces(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    sender_key, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        register_wallet(session, public_key_hex=sender_public, label="Sender")
        register_wallet(session, public_key_hex=receiver_public, label="Receiver")
        add_ledger_entry(
            session,
            entry_type="test_funding",
            from_account=TREASURY_ACCOUNT,
            to_account=sender_address,
            amount_microunits=10_000_000,
            reference="test-funding",
        )

        transfer_payload = wallet_transfer_payload(
            from_address=sender_address,
            to_address=receiver_address,
            amount_microunits=1_000_000,
            nonce=True,
            memo="bool nonce",
        )
        with pytest.raises(LedgerError, match="nonce must be an integer"):
            submit_wallet_transfer(
                session,
                from_address=sender_address,
                to_address=receiver_address,
                amount_mrwk="1",
                nonce=True,
                memo="bool nonce",
                signature_hex=_sign(sender_key, transfer_payload),
            )

        link_payload = wallet_link_payload(
            address=sender_address,
            github_login="alice",
            nonce=True,
        )
        with pytest.raises(LedgerError, match="nonce must be an integer"):
            link_wallet_to_github(
                session,
                address=sender_address,
                github_login="alice",
                nonce=True,
                signature_hex=_sign(sender_key, link_payload),
            )

        valid_link_payload = wallet_link_payload(
            address=sender_address,
            github_login="alice",
            nonce=1,
        )
        link_wallet_to_github(
            session,
            address=sender_address,
            github_login="alice",
            nonce=1,
            signature_hex=_sign(sender_key, valid_link_payload),
        )
        claim_payload = wallet_claim_payload(
            address=sender_address,
            github_login="alice",
            nonce=True,
        )
        with pytest.raises(LedgerError, match="nonce must be an integer"):
            submit_github_claim(
                session,
                address=sender_address,
                github_login="alice",
                nonce=True,
                signature_hex=_sign(sender_key, claim_payload),
            )


def test_linked_wallet_can_claim_existing_github_balance(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    private_key, public_hex, address = _keypair()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        register_wallet(session, public_key_hex=public_hex, label="Alice")
        add_ledger_entry(
            session,
            entry_type="legacy_payment",
            from_account=TREASURY_ACCOUNT,
            to_account="github:alice",
            amount_microunits=25_000_000,
            reference="legacy",
        )

        link_payload = wallet_link_payload(address=address, github_login="alice", nonce=1)
        link_wallet_to_github(
            session,
            address=address,
            github_login="alice",
            nonce=1,
            signature_hex=_sign(private_key, link_payload),
        )
        claim_payload = wallet_claim_payload(address=address, github_login="alice", nonce=2)
        entry = submit_github_claim(
            session,
            address=address,
            github_login="alice",
            nonce=2,
            signature_hex=_sign(private_key, claim_payload),
        )

        assert entry.entry_type == "github_claim"
        assert get_balance(session, "github:alice") == 0
        assert get_balance(session, address) == 25_000_000


def test_github_claim_rejects_wrong_linked_login_without_moving_balance(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    private_key, public_hex, address = _keypair()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        register_wallet(session, public_key_hex=public_hex, label="Alice")
        add_ledger_entry(
            session,
            entry_type="legacy_payment",
            from_account=TREASURY_ACCOUNT,
            to_account="github:alice",
            amount_microunits=25_000_000,
            reference="alice-legacy",
        )
        add_ledger_entry(
            session,
            entry_type="legacy_payment",
            from_account=TREASURY_ACCOUNT,
            to_account="github:bob",
            amount_microunits=10_000_000,
            reference="bob-legacy",
        )
        link_payload = wallet_link_payload(address=address, github_login="alice", nonce=1)
        link_wallet_to_github(
            session,
            address=address,
            github_login="alice",
            nonce=1,
            signature_hex=_sign(private_key, link_payload),
        )

        wrong_login_payload = wallet_claim_payload(address=address, github_login="bob", nonce=2)
        with pytest.raises(LedgerError, match="wallet is not linked to github login"):
            submit_github_claim(
                session,
                address=address,
                github_login="bob",
                nonce=2,
                signature_hex=_sign(private_key, wrong_login_payload),
            )

        assert get_balance(session, "github:alice") == 25_000_000
        assert get_balance(session, "github:bob") == 10_000_000
        assert get_balance(session, address) == 0

        claim_payload = wallet_claim_payload(address=address, github_login="alice", nonce=2)
        submit_github_claim(
            session,
            address=address,
            github_login="alice",
            nonce=2,
            signature_hex=_sign(private_key, claim_payload),
        )

        assert get_balance(session, "github:alice") == 0
        assert get_balance(session, "github:bob") == 10_000_000
        assert get_balance(session, address) == 25_000_000


def test_zero_balance_github_claim_rejects_without_advancing_nonce(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    private_key, public_hex, address = _keypair()

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        register_wallet(session, public_key_hex=public_hex, label="Alice")
        link_payload = wallet_link_payload(address=address, github_login="alice", nonce=1)
        link_wallet_to_github(
            session,
            address=address,
            github_login="alice",
            nonce=1,
            signature_hex=_sign(private_key, link_payload),
        )

        claim_payload = wallet_claim_payload(address=address, github_login="alice", nonce=2)
        claim_signature = _sign(private_key, claim_payload)
        with pytest.raises(LedgerError, match="no github balance to claim"):
            submit_github_claim(
                session,
                address=address,
                github_login="alice",
                nonce=2,
                signature_hex=claim_signature,
            )

        wallet = session.get(Wallet, address)
        assert wallet is not None
        assert wallet.nonce == 1
        add_ledger_entry(
            session,
            entry_type="legacy_payment",
            from_account=TREASURY_ACCOUNT,
            to_account="github:alice",
            amount_microunits=12_000_000,
            reference="late-legacy",
        )
        submit_github_claim(
            session,
            address=address,
            github_login="alice",
            nonce=2,
            signature_hex=claim_signature,
        )

        assert wallet.nonce == 2
        assert get_balance(session, "github:alice") == 0
        assert get_balance(session, address) == 12_000_000
