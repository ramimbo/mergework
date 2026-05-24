from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import (
    TREASURY_ACCOUNT,
    LedgerError,
    add_ledger_entry,
    ensure_genesis,
    register_wallet,
    submit_wallet_transfer,
    wallet_claim_payload,
    wallet_link_payload,
)
from app.main import _signed_value, create_app
from app.wallets import address_from_public_key_hex, canonical_wallet_json


def _keypair() -> tuple[Ed25519PrivateKey, str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_hex = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        .hex()
    )
    return private_key, public_hex, address_from_public_key_hex(public_hex)


def _sign(private_key: Ed25519PrivateKey, payload: dict[str, object]) -> str:
    return private_key.sign(canonical_wallet_json(payload).encode()).hex()


def _register_wallet(client: TestClient, public_key_hex: str, label: str | None = None) -> dict:
    body = {"public_key_hex": public_key_hex}
    if label is not None:
        body["label"] = label
    response = client.post("/api/v1/wallets/register", json=body)
    assert response.status_code == 200
    return response.json()


def _fund_wallet(sqlite_url: str, address: str, amount_microunits: int = 10_000_000) -> None:
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        add_ledger_entry(
            session,
            entry_type="test_funding",
            from_account=TREASURY_ACCOUNT,
            to_account=address,
            amount_microunits=amount_microunits,
            reference=f"test-funding:{address}",
        )


def test_wallet_api_register_lookup_and_transfer(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    sender_key, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    sender = _register_wallet(client, sender_public, "Sender")
    receiver = _register_wallet(client, receiver_public, "Receiver")
    assert sender["address"] == sender_address
    assert receiver["address"] == receiver_address

    _fund_wallet(sqlite_url, sender_address)

    payload = {
        "type": "mrwk_transfer_v1",
        "from_address": sender_address,
        "to_address": receiver_address,
        "amount_microunits": 3_000_000,
        "nonce": 1,
        "memo": "api transfer",
    }
    transfer = client.post(
        "/api/v1/transfers",
        json={
            "from_address": sender_address,
            "to_address": receiver_address,
            "amount_mrwk": "3",
            "nonce": 1,
            "memo": "api transfer",
            "signature_hex": _sign(sender_key, payload),
        },
    ).json()

    assert transfer["type"] == "wallet_transfer"
    assert transfer["amount_mrwk"] == "3"
    assert client.get(f"/api/v1/wallets/{receiver_address}").json()["balance_mrwk"] == "3"


@pytest.mark.parametrize(
    ("body_overrides", "payload_overrides", "expected_detail"),
    [
        ({"nonce": 2}, {"nonce": 2}, "invalid nonce"),
        ({"to_address": "mrwk1" + ("0" * 40)}, {}, "wallet not found"),
        ({"amount_mrwk": "1.0000001"}, {}, "MRWK supports at most 6 decimal places"),
        ({"memo": "x" * 241}, {"memo": "x" * 241}, "memo is too long"),
    ],
)
def test_wallet_transfer_api_rejects_invalid_requests(
    sqlite_url: str,
    body_overrides: dict[str, object],
    payload_overrides: dict[str, object],
    expected_detail: str,
) -> None:
    create_schema(sqlite_url)
    sender_key, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _register_wallet(client, sender_public)
    _register_wallet(client, receiver_public)
    _fund_wallet(sqlite_url, sender_address)

    payload = {
        "type": "mrwk_transfer_v1",
        "from_address": sender_address,
        "to_address": receiver_address,
        "amount_microunits": 1_000_000,
        "nonce": 1,
        "memo": "",
        **payload_overrides,
    }
    body = {
        "from_address": sender_address,
        "to_address": receiver_address,
        "amount_mrwk": "1",
        "nonce": 1,
        "memo": "",
        "signature_hex": _sign(sender_key, payload),
        **body_overrides,
    }

    response = client.post("/api/v1/transfers", json=body)

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


def test_wallet_transfer_api_returns_validation_error(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    _, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _register_wallet(client, sender_public)
    _register_wallet(client, receiver_public)

    response = client.post(
        "/api/v1/transfers",
        json={
            "from_address": sender_address,
            "to_address": receiver_address,
            "amount_mrwk": "1",
            "nonce": 1,
            "memo": "",
            "signature_hex": "00" * 64,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] in {"invalid signature", "insufficient balance"}


def test_wallet_api_malformed_register_requests_return_4xx(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    missing_key = client.post("/api/v1/wallets/register", json={"label": "No key"})
    assert missing_key.status_code == 400
    assert missing_key.json()["detail"] == "public_key_hex is required"

    non_object = client.post("/api/v1/wallets/register", json=["not", "an", "object"])
    assert non_object.status_code == 400
    assert non_object.json()["detail"] == "json body must be an object"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/wallets/register", "/api/v1/wallets/link-github"],
)
def test_wallet_action_get_routes_report_method_not_allowed(sqlite_url: str, path: str) -> None:
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get(path)

    assert response.status_code == 405
    assert response.headers["allow"] == "POST"
    assert response.json()["detail"] == "Method Not Allowed"


def test_wallet_api_malformed_transfer_requests_return_4xx(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    _, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    client.post("/api/v1/wallets/register", json={"public_key_hex": sender_public})
    client.post("/api/v1/wallets/register", json={"public_key_hex": receiver_public})

    missing_sender = client.post(
        "/api/v1/transfers",
        json={
            "to_address": receiver_address,
            "amount_mrwk": "1",
            "nonce": 1,
            "signature_hex": "00" * 64,
        },
    )
    assert missing_sender.status_code == 400
    assert missing_sender.json()["detail"] == "from_address is required"

    malformed_nonce = client.post(
        "/api/v1/transfers",
        json={
            "from_address": sender_address,
            "to_address": receiver_address,
            "amount_mrwk": "1",
            "nonce": "not-an-int",
            "signature_hex": "00" * 64,
        },
    )
    assert malformed_nonce.status_code == 400
    assert malformed_nonce.json()["detail"] == "nonce must be an integer"


def test_wallet_api_malformed_link_and_claim_requests_return_4xx(
    sqlite_url: str, monkeypatch
) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    create_schema(sqlite_url)
    _, public_hex, _ = _keypair()
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    client.cookies.set("mrwk_user", _signed_value("alice", "test-cookie-secret"))
    client.post("/api/v1/wallets/register", json={"public_key_hex": public_hex})

    missing_signature = client.post(
        "/api/v1/wallets/link-github",
        json={"address": "mrwk1" + "0" * 40, "nonce": 1},
    )
    assert missing_signature.status_code == 400
    assert missing_signature.json()["detail"] == "signature_hex is required"

    malformed_nonce = client.post(
        "/api/v1/github/claim",
        json={
            "address": "mrwk1" + "0" * 40,
            "nonce": None,
            "signature_hex": "00" * 64,
        },
    )
    assert malformed_nonce.status_code == 400
    assert malformed_nonce.json()["detail"] == "nonce must be an integer"


def test_wallet_link_and_claim_require_github_login(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    private_key, public_hex, address = _keypair()
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    _register_wallet(client, public_hex)

    link_payload = {
        "type": "mrwk_link_github_v1",
        "address": address,
        "github_login": "alice",
        "nonce": 1,
    }
    unauthorized = client.post(
        "/api/v1/wallets/link-github",
        json={"address": address, "nonce": 1, "signature_hex": _sign(private_key, link_payload)},
    )
    assert unauthorized.status_code == 401

    client.cookies.set("mrwk_user", "alice|9999999999|bad")
    still_unauthorized = client.post(
        "/api/v1/wallets/link-github",
        json={"address": address, "nonce": 1, "signature_hex": _sign(private_key, link_payload)},
    )
    assert still_unauthorized.status_code == 401


def test_github_session_can_link_and_claim_wallet(sqlite_url: str, monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    create_schema(sqlite_url)
    private_key, public_hex, address = _keypair()
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    client.cookies.set("mrwk_user", _signed_value("alice", "test-cookie-secret"))
    _register_wallet(client, public_hex)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        add_ledger_entry(
            session,
            entry_type="legacy_payment",
            from_account=TREASURY_ACCOUNT,
            to_account="github:alice",
            amount_microunits=4_000_000,
            reference="legacy",
        )

    link_payload = wallet_link_payload(address=address, github_login="alice", nonce=1)
    linked = client.post(
        "/api/v1/wallets/link-github",
        json={"address": address, "nonce": 1, "signature_hex": _sign(private_key, link_payload)},
    )
    assert linked.status_code == 200
    assert linked.json()["github_login"] == "alice"

    claim_payload = wallet_claim_payload(address=address, github_login="alice", nonce=2)
    claimed = client.post(
        "/api/v1/github/claim",
        json={"address": address, "nonce": 2, "signature_hex": _sign(private_key, claim_payload)},
    )
    assert claimed.status_code == 200
    assert claimed.json()["type"] == "github_claim"
    assert client.get(f"/api/v1/wallets/{address}").json()["balance_mrwk"] == "4"


def test_auth_routes_exist_when_oauth_is_unconfigured(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    assert client.get("/auth/github/login").status_code == 503
    assert client.get("/api/v1/auth/me").json()["authenticated"] is False


def test_github_login_redirects_when_oauth_is_configured(sqlite_url: str, monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    monkeypatch.setenv("MERGEWORK_PUBLIC_BASE_URL", "https://mrwk.example.test")
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/auth/github/login?next=/me", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=client-id" in response.headers["location"]
    assert "mrwk_oauth_state" in response.cookies


def test_wallet_pages_expose_transfer_and_github_claim_flows(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    _, public_hex, address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _register_wallet(client, public_hex, "Main smoke wallet")

    wallets = client.get("/wallets").text
    detail = client.get(f"/wallets/{address}").text
    transfer = client.get("/transfer").text
    me = client.get("/me").text

    assert "Generate wallet" in wallets
    assert "Private key stays in this browser" in wallets
    assert "If you lose the private key" in wallets
    assert address in detail
    assert "Main smoke wallet" in wallets
    assert "Main smoke wallet" in detail
    assert "Signed transfer" in transfer
    assert "both wallets are registered" in transfer
    assert "/static/wallet.js" in transfer
    assert "Link a wallet" in me


def test_wallet_pages_do_not_require_manual_nonce(sqlite_url: str, monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    client.cookies.set("mrwk_user", _signed_value("alice", "test-cookie-secret"))

    transfer = client.get("/transfer").text
    me = client.get("/me").text

    assert 'name="nonce"' not in transfer
    assert 'name="nonce"' not in me
    assert "Transaction number is handled automatically" in transfer
    assert "Transaction number is handled automatically" in me
    assert "different key will be rejected" in me
    assert "GitHub account is linked to the wallet" in me


def test_reject_self_transfer(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)

    _, public_hex, address = _keypair()

    with session_scope(sqlite_url) as session:
        register_wallet(session, public_key_hex=public_hex)

    with (
        pytest.raises(LedgerError, match="sender and receiver must be different"),
        session_scope(sqlite_url) as session,
    ):
        submit_wallet_transfer(
            session,
            from_address=address,
            to_address=address,
            amount_mrwk="1",
            nonce=1,
            memo="",
            signature_hex="a" * 128,
        )
