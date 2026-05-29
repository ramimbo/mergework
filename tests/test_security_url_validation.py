from __future__ import annotations

import pytest

from app.db import create_schema, session_scope
from app.ledger.service import (
    LedgerError,
    create_bounty,
    ensure_genesis,
    pay_bounty,
    public_url_or_none,
    validate_public_url,
)


def test_bounty_urls_reject_unsafe_schemes(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        with pytest.raises(LedgerError, match="URL must use http or https"):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=7,
                issue_url="javascript:alert(1)",
                title="Unsafe URL",
                reward_mrwk="1",
                acceptance="Maintainer applies mrwk:accepted",
            )
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=8,
            issue_url="https://github.com/ramimbo/mergework/issues/8",
            title="Safe URL",
            reward_mrwk="1",
            acceptance="Maintainer applies mrwk:accepted",
        )
        with pytest.raises(LedgerError, match="URL must use http or https"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:alice",
                submission_url="javascript:alert(1)",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
            )


def test_public_urls_reject_malformed_hosts_and_ports() -> None:
    for url in (
        "https://[bad",
        "https://example.com:bad/path",
        "https://example.com:/path",
        "https://:443/path",
        "https://999.999.999.999/path",
        "https://01.02.03.04/path",
        "https://api..example/path",
        "https://bad_host.example/path",
        "https://-bad.example/path",
        "https://bad-.example/path",
    ):
        with pytest.raises(LedgerError, match="URL must include a valid host"):
            validate_public_url(url)

    assert public_url_or_none("https://[bad") is None
    assert public_url_or_none("https://:443/path") is None
    assert public_url_or_none("https://999.999.999.999/path") is None
    assert public_url_or_none("https://bad_host.example/path") is None


def test_bounty_urls_reject_embedded_credentials(sqlite_url: str) -> None:
    with pytest.raises(LedgerError, match="URL must not include credentials"):
        validate_public_url("https://@github.com/ramimbo/mergework/issues/9")
    assert public_url_or_none("https://@github.com/ramimbo/mergework/issues/9") is None

    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        with pytest.raises(LedgerError, match="URL must not include credentials"):
            create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=9,
                issue_url="https://operator:secret@github.com/ramimbo/mergework/issues/9",
                title="Credential-bearing URL",
                reward_mrwk="1",
                acceptance="Maintainer applies mrwk:accepted",
            )
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=10,
            issue_url="https://github.com/ramimbo/mergework/issues/10",
            title="Safe URL",
            reward_mrwk="1",
            acceptance="Maintainer applies mrwk:accepted",
        )
        with pytest.raises(LedgerError, match="URL must not include credentials"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:alice",
                submission_url="https://operator:secret@github.com/ramimbo/mergework/pull/10",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
            )


def test_public_urls_reject_whitespace() -> None:
    for url in (
        "https://exa mple.com/path",
        "https://example.com/has space",
        "https://example.com/?q=has space",
    ):
        with pytest.raises(LedgerError, match="URL must not contain whitespace"):
            validate_public_url(url)
        assert public_url_or_none(url) is None

    assert validate_public_url(" https://example.com/path ") == "https://example.com/path"


def test_bounty_urls_reject_non_public_hosts(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        for issue_number, issue_url in enumerate(
            (
                "https://localhost/ramimbo/mergework/issues/21",
                "https://internal/ramimbo/mergework/issues/21",
                "https://127.0.0.1/ramimbo/mergework/issues/21",
                "https://10.0.0.5/ramimbo/mergework/issues/21",
                "https://100.64.0.1/ramimbo/mergework/issues/21",
                "https://169.254.10.20/ramimbo/mergework/issues/21",
                "https://224.0.0.1/ramimbo/mergework/issues/21",
                "https://[::1]/ramimbo/mergework/issues/21",
                "https://[fd00::1]/ramimbo/mergework/issues/21",
            ),
            start=21,
        ):
            with pytest.raises(LedgerError, match="URL must use a public host"):
                create_bounty(
                    session,
                    repo="ramimbo/mergework",
                    issue_number=issue_number,
                    issue_url=issue_url,
                    title="Non-public URL",
                    reward_mrwk="1",
                    acceptance="Maintainer applies mrwk:accepted",
                )

        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=27,
            issue_url="https://github.com/ramimbo/mergework/issues/27",
            title="Safe URL",
            reward_mrwk="1",
            acceptance="Maintainer applies mrwk:accepted",
        )
        with pytest.raises(LedgerError, match="URL must use a public host"):
            pay_bounty(
                session,
                bounty_id=bounty.id,
                to_account="github:alice",
                submission_url="https://192.168.1.20/ramimbo/mergework/pull/27",
                accepted_by="maintainer",
                verifier_result={"label": "mrwk:accepted"},
            )
