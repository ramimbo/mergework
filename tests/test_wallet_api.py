from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import (
    TREASURY_ACCOUNT,
    add_ledger_entry,
    ensure_genesis,
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


def test_wallet_api_register_lookup_and_transfer(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    sender_key, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    sender = client.post(
        "/api/v1/wallets/register",
        json={"public_key_hex": sender_public, "label": "Sender"},
    ).json()
    receiver = client.post(
        "/api/v1/wallets/register",
        json={"public_key_hex": receiver_public, "label": "Receiver"},
    ).json()
    assert sender["address"] == sender_address
    assert receiver["address"] == receiver_address

    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        add_ledger_entry(
            session,
            entry_type="test_funding",
            from_account=TREASURY_ACCOUNT,
            to_account=sender_address,
            amount_microunits=10_000_000,
            reference="test-funding",
        )

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


def test_wallet_transfer_api_returns_validation_error(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    _, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    client.post("/api/v1/wallets/register", json={"public_key_hex": sender_public})
    client.post("/api/v1/wallets/register", json={"public_key_hex": receiver_public})

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


def test_wallet_transfer_api_rejects_wrong_signer(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    _, sender_public, sender_address = _keypair()
    wrong_key, _, _ = _keypair()
    _, receiver_public, receiver_address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    client.post("/api/v1/wallets/register", json={"public_key_hex": sender_public})
    client.post("/api/v1/wallets/register", json={"public_key_hex": receiver_public})
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        add_ledger_entry(
            session,
            entry_type="test_funding",
            from_account=TREASURY_ACCOUNT,
            to_account=sender_address,
            amount_microunits=10_000_000,
            reference="test-funding-wrong-signer",
        )

    payload = {
        "type": "mrwk_transfer_v1",
        "from_address": sender_address,
        "to_address": receiver_address,
        "amount_microunits": 1_000_000,
        "nonce": 1,
        "memo": "",
    }

    response = client.post(
        "/api/v1/transfers",
        json={
            "from_address": sender_address,
            "to_address": receiver_address,
            "amount_mrwk": "1",
            "nonce": 1,
            "memo": "",
            "signature_hex": _sign(wrong_key, payload),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid signature"


def test_wallet_transfer_api_rejects_insufficient_balance(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    sender_key, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    client.post("/api/v1/wallets/register", json={"public_key_hex": sender_public})
    client.post("/api/v1/wallets/register", json={"public_key_hex": receiver_public})

    payload = {
        "type": "mrwk_transfer_v1",
        "from_address": sender_address,
        "to_address": receiver_address,
        "amount_microunits": 1_000_000,
        "nonce": 1,
        "memo": "",
    }

    response = client.post(
        "/api/v1/transfers",
        json={
            "from_address": sender_address,
            "to_address": receiver_address,
            "amount_mrwk": "1",
            "nonce": 1,
            "memo": "",
            "signature_hex": _sign(sender_key, payload),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "insufficient balance"


def test_wallet_link_and_claim_require_github_login(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    private_key, public_hex, address = _keypair()
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    client.post("/api/v1/wallets/register", json={"public_key_hex": public_hex})

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
    client.post("/api/v1/wallets/register", json={"public_key_hex": public_hex})
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
    client.post("/api/v1/wallets/register", json={"public_key_hex": public_hex})

    wallets = client.get("/wallets").text
    detail = client.get(f"/wallets/{address}").text
    transfer = client.get("/transfer").text
    me = client.get("/me").text

    assert "Generate wallet" in wallets
    assert "Private key stays in this browser" in wallets
    assert address in detail
    assert "Signed transfer" in transfer
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
