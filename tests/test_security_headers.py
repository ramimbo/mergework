from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis
from app.main import create_app
from app.security_headers import API_DOCS_CSP, SECURITY_HEADERS


def test_security_header_defaults_are_applied_to_browser_routes(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/")

    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name.lower()] == value


def test_security_header_middleware_preserves_head_as_get(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Security header middleware",
            reward_mrwk="25",
            acceptance="HEAD requests should keep GET route semantics without a body.",
        )
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.head("/api/v1/bounties")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "0"
    assert response.headers["x-frame-options"] == "DENY"


def test_api_docs_use_relaxed_docs_csp(sqlite_url: str) -> None:
    client = TestClient(create_app(database_url=sqlite_url, webhook_secret="secret"))

    response = client.get("/api/docs")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == API_DOCS_CSP


def test_forwarded_https_redirects_keep_https_scheme(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Forwarded HTTPS redirect",
            reward_mrwk="25",
            acceptance="Trailing slash redirects should not downgrade public HTTPS requests.",
        )
    client = TestClient(
        create_app(database_url=sqlite_url, webhook_secret="secret"),
        base_url="http://mrwk.ltclab.site",
    )

    response = client.get(
        f"/bounties/{bounty.id}/",
        headers={"x-forwarded-proto": "https"},
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers["location"] == f"https://mrwk.ltclab.site/bounties/{bounty.id}"
