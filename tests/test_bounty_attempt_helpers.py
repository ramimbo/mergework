from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.bounty_attempts import (
    attempt_effective_status,
    bounty_attempt_to_dict,
    bounty_attempt_warnings,
    expire_stale_bounty_attempts,
)
from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty
from app.models import BountyAttempt


def test_bounty_attempt_dict_reports_expired_active_attempt(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Attempt helper extraction",
            reward_mrwk="25",
            acceptance="Attempt serializers should be testable outside app.main.",
        )
        attempt = BountyAttempt(
            bounty_id=bounty.id,
            submitter_account="github:alice",
            source_url="https://github.com/ramimbo/mergework/pull/320",
            status="active",
            expires_at=now - timedelta(minutes=1),
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
        session.add(attempt)
        session.flush()

        payload = bounty_attempt_to_dict(attempt, now)

    assert attempt_effective_status(attempt, now) == "expired"
    assert payload["status"] == "expired"
    assert payload["submitter_account"] == "github:alice"
    assert payload["expires_at"] == "2026-05-26T11:59:00+00:00"


def test_bounty_attempt_warnings_and_expiration_helpers(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Attempt warnings",
            reward_mrwk="25",
            max_awards=2,
            acceptance="Attempt warnings should count active overlapping work.",
        )
        session.add_all(
            [
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:alice",
                    source_url=None,
                    status="active",
                    expires_at=now + timedelta(hours=1),
                    created_at=now - timedelta(minutes=5),
                    updated_at=now - timedelta(minutes=5),
                ),
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:bob",
                    source_url=None,
                    status="active",
                    expires_at=now - timedelta(minutes=1),
                    created_at=now - timedelta(minutes=10),
                    updated_at=now - timedelta(minutes=10),
                ),
            ]
        )
        session.flush()

        expire_stale_bounty_attempts(session, bounty.id, now, "github:bob")
        active_warning = bounty_attempt_warnings(session, bounty, now)
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:winner",
            submission_url="https://github.com/ramimbo/mergework/pull/321",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:second",
            submission_url="https://github.com/ramimbo/mergework/pull/322",
            accepted_by="maintainer",
            verifier_result={"label": "mrwk:accepted"},
        )
        paid_warning = bounty_attempt_warnings(session, bounty, now)
        bob_attempt = session.scalar(
            select(BountyAttempt).where(BountyAttempt.submitter_account == "github:bob").limit(1)
        )

    assert active_warning == []
    assert paid_warning == ["bounty is paid", "bounty has no award slots remaining"]
    assert bob_attempt is not None
    assert bob_attempt.status == "expired"
