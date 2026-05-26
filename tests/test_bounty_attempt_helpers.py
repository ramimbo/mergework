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
from app.ledger.service import create_bounty, ensure_genesis
from app.models import BountyAttempt


def test_bounty_attempt_dict_reports_effective_expired_status() -> None:
    now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    attempt = BountyAttempt(
        id=7,
        bounty_id=3,
        submitter_account="github:alice",
        source_url="https://github.com/ramimbo/mergework/pull/700",
        status="active",
        expires_at=now - timedelta(minutes=1),
        created_at=(now - timedelta(hours=1)).replace(tzinfo=None),
        updated_at=now.replace(tzinfo=None),
    )

    assert attempt_effective_status(attempt, now) == "expired"
    assert bounty_attempt_to_dict(attempt, now) == {
        "id": 7,
        "bounty_id": 3,
        "submitter_account": "github:alice",
        "source_url": "https://github.com/ramimbo/mergework/pull/700",
        "status": "expired",
        "expires_at": "2026-05-26T11:59:00+00:00",
        "created_at": "2026-05-26T11:00:00+00:00",
        "updated_at": "2026-05-26T12:00:00+00:00",
    }


def test_bounty_attempt_helpers_expire_stale_attempts_and_warn_on_overlap(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=370,
            issue_url="https://github.com/ramimbo/mergework/issues/370",
            title="Attempt helper extraction",
            reward_mrwk="100",
            max_awards=2,
            acceptance="Attempt helpers should stay consistent.",
        )
        session.add_all(
            [
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:alice",
                    source_url=None,
                    status="active",
                    expires_at=now - timedelta(minutes=5),
                    created_at=now - timedelta(hours=1),
                    updated_at=now - timedelta(hours=1),
                ),
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:bob",
                    source_url=None,
                    status="active",
                    expires_at=now + timedelta(hours=1),
                    created_at=now,
                    updated_at=now,
                ),
                BountyAttempt(
                    bounty_id=bounty.id,
                    submitter_account="github:carol",
                    source_url=None,
                    status="active",
                    expires_at=now + timedelta(hours=2),
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        session.flush()

        expire_stale_bounty_attempts(session, bounty.id, now, "github:alice")
        statuses = dict(
            session.execute(
                select(BountyAttempt.submitter_account, BountyAttempt.status).where(
                    BountyAttempt.bounty_id == bounty.id
                )
            ).all()
        )

        assert statuses == {
            "github:alice": "expired",
            "github:bob": "active",
            "github:carol": "active",
        }
        assert bounty_attempt_warnings(session, bounty, now) == ["bounty has 2 active attempts"]
