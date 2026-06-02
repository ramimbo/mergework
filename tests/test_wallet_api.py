from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
    wallet_transfer_payload,
)
from app.main import _safe_next_path, _signed_value, _verified_value, create_app
from app.models import Wallet
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


def test_wallet_transfer_api_rejects_replayed_signed_body(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    sender_key, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _register_wallet(client, sender_public)
    _register_wallet(client, receiver_public)
    _fund_wallet(sqlite_url, sender_address)
    payload = wallet_transfer_payload(
        from_address=sender_address,
        to_address=receiver_address,
        amount_microunits=1_000_000,
        nonce=1,
        memo="api replay",
    )
    body = {
        "from_address": sender_address,
        "to_address": receiver_address,
        "amount_mrwk": "1",
        "nonce": 1,
        "memo": "api replay",
        "signature_hex": _sign(sender_key, payload),
    }

    first = client.post("/api/v1/transfers", json=body)
    second = client.post("/api/v1/transfers", json=body)

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["detail"] == "invalid nonce"


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


def test_wallet_transfer_api_rejects_c1_nonce_before_integer_parsing(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/api/v1/transfers",
        json={
            "from_address": "mrwk1" + ("a" * 40),
            "to_address": "mrwk1" + ("b" * 40),
            "amount_mrwk": "1",
            "nonce": "\u00851",
            "memo": "",
            "signature_hex": "00" * 64,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "nonce must not contain control characters"


def test_wallet_register_api_rejects_label_control_characters(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    _, public_hex, _ = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/api/v1/wallets/register",
        json={"public_key_hex": public_hex, "label": "Main\nWallet"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "wallet label must not contain control characters"


@pytest.mark.parametrize("public_key_hex", ["\t{public}", "{public}\n", "\x85{public}"])
def test_wallet_register_api_rejects_public_key_control_characters(
    sqlite_url: str, public_key_hex: str
) -> None:
    create_schema(sqlite_url)
    _, public_hex, _ = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.post(
        "/api/v1/wallets/register",
        json={"public_key_hex": public_key_hex.format(public=public_hex)},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "public key must not contain control characters"


def test_wallet_transfer_api_rejects_memo_control_characters(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    sender_key, sender_public, sender_address = _keypair()
    _, receiver_public, receiver_address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _register_wallet(client, sender_public)
    _register_wallet(client, receiver_public)
    _fund_wallet(sqlite_url, sender_address)
    memo = "line1\nline2"
    payload = {
        "type": "mrwk_transfer_v1",
        "from_address": sender_address,
        "to_address": receiver_address,
        "amount_microunits": 1_000_000,
        "nonce": 1,
        "memo": memo,
    }

    response = client.post(
        "/api/v1/transfers",
        json={
            "from_address": sender_address,
            "to_address": receiver_address,
            "amount_mrwk": "1",
            "nonce": 1,
            "memo": memo,
            "signature_hex": _sign(sender_key, payload),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "memo must not contain control characters"


@pytest.mark.parametrize("pad", ["\t", "\n", "\x85"])
def test_wallet_transfer_api_rejects_address_and_signature_control_characters(
    sqlite_url: str, pad: str
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
    }
    signature = _sign(sender_key, payload)

    control_address = client.post(
        "/api/v1/transfers",
        json={
            "from_address": f"{pad}{sender_address}",
            "to_address": receiver_address,
            "amount_mrwk": "1",
            "nonce": 1,
            "memo": "",
            "signature_hex": signature,
        },
    )
    control_to_address = client.post(
        "/api/v1/transfers",
        json={
            "from_address": sender_address,
            "to_address": f"{pad}{receiver_address}",
            "amount_mrwk": "1",
            "nonce": 1,
            "memo": "",
            "signature_hex": signature,
        },
    )
    control_signature = client.post(
        "/api/v1/transfers",
        json={
            "from_address": sender_address,
            "to_address": receiver_address,
            "amount_mrwk": "1",
            "nonce": 1,
            "memo": "",
            "signature_hex": f"{pad}{signature}",
        },
    )

    assert control_address.status_code == 400
    assert (
        control_address.json()["detail"]
        == "MRWK wallet address must not contain control characters"
    )
    assert control_to_address.status_code == 400
    assert (
        control_to_address.json()["detail"]
        == "MRWK wallet address must not contain control characters"
    )
    assert control_signature.status_code == 400
    assert control_signature.json()["detail"] == "signature must not contain control characters"


def test_wallet_api_malformed_register_requests_return_4xx(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    missing_key = client.post("/api/v1/wallets/register", json={"label": "No key"})
    assert missing_key.status_code == 400
    assert missing_key.json()["detail"] == "public_key_hex is required"

    non_object = client.post("/api/v1/wallets/register", json=["not", "an", "object"])
    assert non_object.status_code == 400
    assert non_object.json()["detail"] == "json body must be an object"


def test_wallet_lookup_rejects_invalid_addresses_before_lookup(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    api_response = client.get("/api/v1/wallets/not-a-wallet")
    page_response = client.get("/wallets/%20%20%20")
    unknown_wallet = client.get("/api/v1/wallets/mrwk1" + ("0" * 40))

    assert api_response.status_code == 400
    assert api_response.json()["detail"] == "invalid MRWK wallet address"
    assert page_response.status_code == 400
    assert "invalid MRWK wallet address" in page_response.text
    assert unknown_wallet.status_code == 404
    assert unknown_wallet.json()["detail"] == "wallet not found"


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


def test_wallet_method_boundary_routes_are_hidden_from_openapi(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths["/api/v1/wallets/register"]) == {"post"}
    assert set(paths["/api/v1/wallets/link-github"]) == {"post"}
    assert set(paths["/api/v1/wallets/{address}"]) == {"get"}
    assert set(paths["/api/v1/github/claim"]) == {"post"}
    assert set(paths["/api/v1/transfers"]) == {"post"}


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

    fractional_nonce = client.post(
        "/api/v1/transfers",
        json={
            "from_address": sender_address,
            "to_address": receiver_address,
            "amount_mrwk": "1",
            "nonce": 1.5,
            "signature_hex": "00" * 64,
        },
    )
    assert fractional_nonce.status_code == 400
    assert fractional_nonce.json()["detail"] == "nonce must be an integer"


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


@pytest.mark.parametrize(
    "next_path",
    (
        "//evil.example/path",
        "/\\evil.example/path",
        "/me\nLocation:https://evil.example",
        "/" + ("a" * 2048),
    ),
)
def test_oauth_next_path_rejects_redirect_ambiguity(next_path: str) -> None:
    assert _safe_next_path(next_path) == "/me"


def test_github_login_stores_safe_default_for_backslash_next(sqlite_url: str, monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("MERGEWORK_GITHUB_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    monkeypatch.setenv("MERGEWORK_PUBLIC_BASE_URL", "https://mrwk.example.test")
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/auth/github/login?next=/%5Cevil.example/path", follow_redirects=False)

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    state_value = _verified_value(query["state"][0], "test-cookie-secret", 600)
    assert state_value is not None
    _nonce, next_path = state_value.split(",", 1)
    assert next_path == "/me"


def test_wallet_pages_expose_transfer_and_github_claim_flows(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    _, public_hex, address = _keypair()
    _, funded_public, funded_address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _register_wallet(client, public_hex, "Main smoke wallet")
    _register_wallet(client, funded_public, "Funded smoke wallet")
    with session_scope(sqlite_url) as session:
        wallet = session.get(Wallet, address)
        assert wallet is not None
        wallet.github_login = "alice-smoke"
    _fund_wallet(sqlite_url, funded_address)
    with session_scope(sqlite_url) as session:
        add_ledger_entry(
            session,
            entry_type="wallet_transfer",
            from_account=funded_address,
            to_account="github:filter-target",
            amount_microunits=0,
            reference="test-wallet-detail-filter",
        )

    wallets = client.get("/wallets").text
    main_search = client.get("/wallets?q=Main").text
    github_search = client.get("/wallets?q=alice-smoke").text
    no_wallet_match = client.get("/wallets?q=missing-wallet").text
    detail = client.get(f"/wallets/{address}").text
    funded_detail = client.get(f"/wallets/{funded_address}").text
    funded_type_filter = client.get(f"/wallets/{funded_address}?type=test_funding").text
    funded_all_type_filter = client.get(f"/wallets/{funded_address}?type=all").text
    funded_all_type_filter_upper = client.get(f"/wallets/{funded_address}?type=ALL").text
    funded_all_type_filter_spaced = client.get(f"/wallets/{funded_address}?type=%20all%20").text
    funded_missing_type = client.get(f"/wallets/{funded_address}?type=bounty_payment").text
    funded_unknown_type = client.get(f"/wallets/{funded_address}?type=not_a_real_type")
    transfer = client.get("/transfer").text
    me = client.get("/me").text

    assert "Generate wallet" in wallets
    assert "Private key stays in this browser" in wallets
    assert "If you lose the private key" in wallets
    assert (
        'id="wallet-private-key" name="private_key_hex" rows="5" readonly autocomplete="off"'
        in wallets
    )
    assert 'autocapitalize="none" spellcheck="false"' in wallets
    assert address in detail
    assert "Main smoke wallet" in wallets
    assert "Main smoke wallet" in detail
    assert "Search wallets" in wallets
    assert "Main smoke wallet" in main_search
    assert "Funded smoke wallet" not in main_search
    assert "alice-smoke" in github_search
    assert 'href="/accounts/github:alice-smoke">alice-smoke</a>' in wallets
    assert 'href="/accounts/github:alice-smoke">alice-smoke</a>' in github_search
    funded_row_start = wallets.index(
        f'<a href="/wallets/{funded_address}"><code>{funded_address}</code></a>'
    )
    funded_row = wallets[funded_row_start : wallets.index("</tr>", funded_row_start)]
    assert "<td>Funded smoke wallet</td>" in funded_row
    assert "<td>\n          -\n        </td>" in funded_row
    assert "/accounts/github:" not in funded_row
    assert "No registered wallets match this search." in no_wallet_match
    assert "To claim GitHub bounty balance" in detail
    assert "No activity yet" in detail
    assert "No activity yet" not in funded_detail
    assert "Filter wallet transactions" in funded_detail
    assert 'value="test_funding" selected' in funded_type_filter
    assert "Showing test_funding transactions." in funded_type_filter
    assert "Showing all transactions." not in funded_all_type_filter
    assert "wallet_transfer" in funded_all_type_filter
    assert "test_funding" in funded_all_type_filter
    assert "No wallet transactions match this type." not in funded_all_type_filter
    assert "wallet_transfer" in funded_all_type_filter_upper
    assert "test_funding" in funded_all_type_filter_upper
    assert "wallet_transfer" in funded_all_type_filter_spaced
    assert "test_funding" in funded_all_type_filter_spaced
    assert "No wallet transactions match this type." in funded_missing_type
    assert funded_unknown_type.status_code == 400
    assert funded_unknown_type.json()["detail"] == (
        "transaction type must be one of: all, bounty_payment, bounty_reserve, "
        "bounty_release, github_claim, wallet_transfer, genesis, test_funding"
    )
    assert "Signed transfer" in transfer
    assert "both wallets are registered" in transfer
    assert "/static/wallet.js" in transfer
    assert "Link a wallet" in me


def test_wallet_pages_reject_control_character_filters(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    _, public_hex, address = _keypair()
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))
    _register_wallet(client, public_hex, "Main smoke wallet")
    _fund_wallet(sqlite_url, address)

    search_response = client.get("/wallets", params={"q": "\u0085Main"})
    type_response = client.get(f"/wallets/{address}", params={"type": "test_funding\t"})
    masked_search_response = client.get("/wallets?q=%C2%85Main&q=Main")
    repeated_search_response = client.get("/wallets?q=Main&q=smoke")
    masked_type_response = client.get(f"/wallets/{address}?type=%C2%85test_funding&type=all")
    repeated_type_response = client.get(f"/wallets/{address}?type=test_funding&type=all")

    assert search_response.status_code == 400
    assert search_response.json()["detail"] == "q must not contain control characters"
    assert type_response.status_code == 400
    assert type_response.json()["detail"] == "transaction type must not contain control characters"
    assert masked_search_response.status_code == 400
    assert masked_search_response.json()["detail"] == "q must not contain control characters"
    assert repeated_search_response.status_code == 400
    assert repeated_search_response.json()["detail"] == "q must be provided at most once"
    assert masked_type_response.status_code == 400
    assert (
        masked_type_response.json()["detail"]
        == "transaction type must not contain control characters"
    )
    assert repeated_type_response.status_code == 400
    assert repeated_type_response.json()["detail"] == "type must be provided at most once"


def test_me_page_shows_signed_in_github_claim_balance(sqlite_url: str, monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    create_schema(sqlite_url)
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
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    client.cookies.set("mrwk_user", _signed_value("alice", "test-cookie-secret"))

    me = client.get("/me").text

    assert "Signed in as alice." in me
    assert "github:alice" in me
    assert "4 MRWK available to claim" in me


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
    assert 'name="private_key_hex" rows="5" autocomplete="off"' in transfer
    assert me.count('name="private_key_hex" rows="5" autocomplete="off"') == 2
    assert "Clear this field after use. Never share your private key." in transfer
    assert "Clear this field after use. Never share your private key." in me


def test_github_wallet_actions_clear_private_key_after_submit_attempt() -> None:
    wallet_js = Path("app/static/wallet.js").read_text(encoding="utf-8")
    set_result = "setText(resultSelector, result);"
    set_error = "setText(resultSelector, error.message);"
    finally_block = "} finally {"
    clear_private_key = "clearPrivateKeyField(form);"
    refresh_nonce = "await getNextNonce(address, statusSelector);"

    assert "function clearPrivateKeyField(form)" in wallet_js
    assert 'for (const action of ["link-github", "claim-github"])' in wallet_js
    idx_set_result = wallet_js.find(set_result)
    idx_refresh_nonce = wallet_js.find(refresh_nonce, idx_set_result)
    idx_set_error = wallet_js.find(set_error, idx_refresh_nonce)
    idx_finally = wallet_js.find(finally_block, idx_set_error)
    idx_clear_private_key = wallet_js.find(clear_private_key, idx_finally)

    assert -1 not in {
        idx_set_result,
        idx_refresh_nonce,
        idx_set_error,
        idx_finally,
        idx_clear_private_key,
    }
    assert idx_set_result < idx_refresh_nonce < idx_set_error < idx_finally < idx_clear_private_key


def test_transfer_action_clears_private_key_after_submit_attempt() -> None:
    wallet_js = Path("app/static/wallet.js").read_text(encoding="utf-8")
    transfer_start = wallet_js.find("function setupTransfer()")
    github_actions_start = wallet_js.find("function setupGithubActions()")
    transfer_block = wallet_js[transfer_start:github_actions_start]

    assert 'form[data-action="submit-transfer"]' in transfer_block
    assert 'setText("[data-transfer-result]", transfer);' in transfer_block
    assert 'setText("[data-transfer-result]", error.message);' in transfer_block
    assert "} finally {" in transfer_block
    assert "clearPrivateKeyField(form);" in transfer_block


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


def test_me_page_prefills_claim_address_for_linked_wallet(sqlite_url: str, monkeypatch) -> None:
    monkeypatch.setenv("MERGEWORK_COOKIE_SECRET", "test-cookie-secret")
    create_schema(sqlite_url)
    _, public_hex, address = _keypair()
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        register_wallet(session, public_key_hex=public_hex, github_login="alice")
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="https://testserver",
    )
    client.cookies.set("mrwk_user", _signed_value("alice", "test-cookie-secret"))

    me = client.get("/me").text

    assert f'value="{address}"' in me
    assert "Claim form is prefilled with your linked wallet." in me


def test_prelinked_wallet_creates_github_account_row(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    _, public_hex, _ = _keypair()
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        register_wallet(session, public_key_hex=public_hex, github_login="alice")

    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    account = client.get("/api/v1/accounts/github:alice")

    assert account.status_code == 200
    assert account.json()["exists"] is True
