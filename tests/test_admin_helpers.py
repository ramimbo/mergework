from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.admin import (
    ADMIN_WEBHOOK_LIMIT_OPTIONS,
    admin_page_context,
    create_admin_bounty_from_form,
    list_webhook_events,
    normalize_webhook_status_filter,
    webhook_events_to_dict,
    webhook_status_summary,
)
from app.db import create_schema, session_scope
from app.models import Bounty, TreasuryProposal, WebhookEvent


def _event(
    delivery_id: str,
    status: str,
    created_at: datetime,
    event_type: str = "pull_request",
    payload_hash: str | None = None,
) -> WebhookEvent:
    return WebhookEvent(
        delivery_id=delivery_id,
        event_type=event_type,
        payload_hash=payload_hash or delivery_id.ljust(64, "0")[:64],
        processed_status=status,
        created_at=created_at,
    )


def test_normalize_webhook_status_filter_trims_and_lowers() -> None:
    assert normalize_webhook_status_filter(None) is None
    assert normalize_webhook_status_filter("   ") is None
    assert normalize_webhook_status_filter(" Missing_Submitter ") == "missing_submitter"


def test_list_webhook_events_filters_and_serializes_safe_fields(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    base_time = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    with session_scope(sqlite_url) as session:
        session.add(_event("delivery-paid", "paid", base_time))
        session.add(
            _event("delivery-missing-old", "missing_submitter", base_time - timedelta(hours=1))
        )
        session.add(
            _event("delivery-missing-new", "Missing_Submitter", base_time + timedelta(hours=1))
        )

    with session_scope(sqlite_url) as session:
        events = list_webhook_events(session, " Missing_Submitter ", limit=10)
        serialized = webhook_events_to_dict(events)

    assert [event.delivery_id for event in events] == [
        "delivery-missing-new",
        "delivery-missing-old",
    ]
    assert serialized == [
        {
            "delivery_id": "delivery-missing-new",
            "event_type": "pull_request",
            "processed_status": "Missing_Submitter",
            "payload_hash": "delivery-missing-new".ljust(64, "0")[:64],
            "created_at": (base_time + timedelta(hours=1)).replace(tzinfo=None).isoformat(),
        },
        {
            "delivery_id": "delivery-missing-old",
            "event_type": "pull_request",
            "processed_status": "missing_submitter",
            "payload_hash": "delivery-missing-old".ljust(64, "0")[:64],
            "created_at": (base_time - timedelta(hours=1)).replace(tzinfo=None).isoformat(),
        },
    ]


def test_webhook_status_summary_uses_admin_scan_order(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    base_time = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    with session_scope(sqlite_url) as session:
        session.add(_event("delivery-paid-1", "paid", base_time))
        session.add(_event("delivery-paid-2", "paid", base_time + timedelta(minutes=1)))
        session.add(
            _event("delivery-missing", "Missing_Submitter", base_time + timedelta(minutes=2))
        )
        session.add(_event("delivery-custom-1", "custom_status", base_time + timedelta(minutes=3)))
        session.add(_event("delivery-custom-2", "custom_status", base_time + timedelta(minutes=4)))
        session.add(_event("delivery-custom-3", "custom_status", base_time + timedelta(minutes=5)))

    with session_scope(sqlite_url) as session:
        summary = webhook_status_summary(session)

    assert summary == [
        {"processed_status": "missing_submitter", "count": 1},
        {"processed_status": "paid", "count": 2},
        {"processed_status": "custom_status", "count": 3},
    ]


def test_admin_page_context_builds_webhook_dashboard_context(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    base_time = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    with session_scope(sqlite_url) as session:
        session.add(_event("delivery-paid", "paid", base_time))
        session.add(_event("delivery-missing", "Missing_Submitter", base_time + timedelta(hours=1)))

    with session_scope(sqlite_url) as session:
        context = admin_page_context(
            session,
            login="maintainer",
            csrf_token="csrf-token",
            webhook_status=" Missing_Submitter ",
            webhook_limit=10,
        )

    assert context["login"] == "maintainer"
    assert context["csrf_token"] == "csrf-token"
    assert context["webhook_status"] == "missing_submitter"
    assert context["webhook_limit"] == 10
    assert context["webhook_limit_options"] == ADMIN_WEBHOOK_LIMIT_OPTIONS
    assert [event.delivery_id for event in context["webhook_events"]] == ["delivery-missing"]
    assert context["webhook_status_summary"] == [
        {"processed_status": "missing_submitter", "count": 1},
        {"processed_status": "paid", "count": 1},
    ]


def test_create_admin_bounty_from_form_returns_created_proposal_id(sqlite_url: str) -> None:
    create_schema(sqlite_url)

    with session_scope(sqlite_url) as session:
        proposal_id = create_admin_bounty_from_form(
            session,
            repo="RAmimbo/MergeWork",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Admin helper bounty",
            reward_mrwk="25",
            max_awards=2,
            acceptance="Admin page form creates this bounty.",
            proposed_by="maintainer",
        )
        proposal = session.get(TreasuryProposal, proposal_id)
        bounty_count = session.scalar(select(func.count(Bounty.id)))

    assert proposal is not None
    assert proposal.action == "create_bounty"
    assert proposal.status == "pending"
    assert proposal.proposed_by == "maintainer"
    assert json.loads(proposal.payload_json) == {
        "acceptance": "Admin page form creates this bounty.",
        "issue_number": 321,
        "issue_url": "https://github.com/ramimbo/mergework/issues/321",
        "max_awards": 2,
        "repo": "ramimbo/mergework",
        "reward_mrwk": "25",
        "title": "Admin helper bounty",
    }
    assert bounty_count == 0
