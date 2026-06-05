from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import event

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty, register_wallet
from app.models import Bounty, Proof, WalletTransfer
from app.serializers import (
    accepted_work_for_account,
    account_accepted_summary,
    activity_to_dict,
    bounties_to_dict,
    bounty_awards_to_dict,
    bounty_list_summary,
    bounty_to_dict,
    empty_accepted_summary,
    safe_accepted_work_for_account,
    safe_account_accepted_summary,
    wallet_to_dict,
    wallet_transfer_to_dict,
)
from app.treasury import propose_treasury_action


class BrokenSession:
    def execute(self, *args, **kwargs):
        raise RuntimeError("database unavailable")


def test_bounty_serializers_preserve_public_capacity_fields(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Refactor public serializers",
            reward_mrwk="25",
            max_awards=4,
            acceptance="Public capacity fields stay stable after extraction.",
        )

        bounty_data = bounty_to_dict(bounty)

    assert bounty_data["reward_mrwk"] == "25"
    assert bounty_data["available_mrwk"] == "100"
    assert bounty_data["reserved_mrwk"] == "100"
    assert bounty_data["awards_remaining"] == 4
    requirements = bounty_data["submission_requirements"]
    assert requirements["submission_mode"] == "pr_or_evidence"
    assert requirements["submission_url_kind"] == "github_pr_or_public_evidence_url"
    assert requirements["expected_artifact"] == "focused PR, issue, report, or evidence URL"
    assert requirements["reference_formats"] == ["Bounty #320", "Refs #320"]
    assert requirements["claim_command"] == "/claim"
    assert bounty_data["created_at"].endswith("Z")
    assert bounty_list_summary([bounty_data]) == {
        "bounties_shown": 1,
        "open_awards": 4,
        "open_pool_mrwk": "100",
        "effective_open_awards": 4,
        "effective_open_pool_mrwk": "100",
        "availability_state_counts": {"open": 1},
        "pending_payout_awards": 0,
        "reduced_capacity_bounties": 0,
        "effectively_unavailable_bounties": 0,
    }


def test_bounty_list_summary_breaks_down_availability_states() -> None:
    summary = bounty_list_summary(
        [
            {
                "reward_mrwk": "25",
                "awards_remaining": 3,
                "effective_awards_remaining": 3,
                "availability_state": "open",
                "pending_payout_awards": 0,
            },
            {
                "reward_mrwk": "40",
                "awards_remaining": 2,
                "effective_awards_remaining": 1,
                "availability_state": "pending_payouts_partial",
                "pending_payout_awards": 1,
            },
            {
                "reward_mrwk": "10",
                "awards_remaining": 2,
                "effective_awards_remaining": 0,
                "availability_state": "pending_payouts_full",
                "pending_payout_awards": 2,
            },
            {
                "reward_mrwk": "15",
                "awards_remaining": 1,
                "effective_awards_remaining": 0,
                "availability_state": "pending_close",
                "pending_payout_awards": 0,
            },
        ]
    )

    assert summary == {
        "bounties_shown": 4,
        "open_awards": 8,
        "open_pool_mrwk": "190",
        "effective_open_awards": 4,
        "effective_open_pool_mrwk": "115",
        "availability_state_counts": {
            "open": 1,
            "pending_payouts_partial": 1,
            "pending_payouts_full": 1,
            "pending_close": 1,
        },
        "pending_payout_awards": 3,
        "reduced_capacity_bounties": 3,
        "effectively_unavailable_bounties": 2,
    }


def test_bounty_list_summary_preserves_fractional_reward_pool_totals() -> None:
    summary = bounty_list_summary(
        [
            {
                "reward_mrwk": "0.5",
                "awards_remaining": 3,
                "effective_awards_remaining": 2,
                "availability_state": "pending_payouts_partial",
                "pending_payout_awards": 1,
            },
            {
                "reward_mrwk": "1.25",
                "awards_remaining": 2,
                "availability_state": "open",
                "pending_payout_awards": 0,
            },
        ]
    )

    assert summary["open_awards"] == 5
    assert summary["open_pool_mrwk"] == "4"
    assert summary["effective_open_awards"] == 4
    assert summary["effective_open_pool_mrwk"] == "3.5"


def test_bounty_serializer_marks_proposed_work_issue_submission_mode(
    sqlite_url: str,
) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=649,
            issue_url="https://github.com/ramimbo/mergework/issues/649",
            title="MRWK bounty: accepted proposed-work requests, round 1",
            reward_mrwk="50",
            max_awards=20,
            acceptance=(
                "Accepted work is a new proposed-work issue in ramimbo/mergework. "
                "Payment review uses the proposed-work issue URL as submission_url "
                "through pay_bounty after maintainer acceptance."
            ),
        )

        requirements = bounty_to_dict(bounty)["submission_requirements"]

    assert requirements["submission_mode"] == "issue"
    assert requirements["submission_url_kind"] == "github_issue"
    assert requirements["expected_artifact"] == "new proposed-work GitHub issue URL"
    assert requirements["attempt_endpoint_applicability"] == ("not_required_for_issue_submission")
    assert requirements["reference_formats"] == [
        "Bounty #649",
        "Refs #649",
        "Linked bounty: #649",
    ]
    assert requirements["claim_command"] == "/claim #649"
    assert "new proposed-work GitHub issue URL" in requirements["evidence_required"]
    next_action_ids = [action["id"] for action in requirements["next_actions"]]
    assert "open_proposed_work_issue" in next_action_ids
    assert "link_bounty_issue" in next_action_ids


def test_bounties_to_dict_preloads_pending_proposals_once(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        payout_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Pending payout serializer",
            reward_mrwk="25",
            max_awards=2,
            acceptance="Pending payout should reduce effective capacity.",
        )
        close_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=322,
            issue_url="https://github.com/ramimbo/mergework/issues/322",
            title="Pending close serializer",
            reward_mrwk="30",
            max_awards=3,
            acceptance="Pending close should hide effective capacity.",
        )
        plain_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=323,
            issue_url="https://github.com/ramimbo/mergework/issues/323",
            title="Plain serializer",
            reward_mrwk="10",
            acceptance="Plain bounty should remain open.",
        )
        propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": payout_bounty.id,
                "to_account": "github:alice",
                "submission_url": "https://github.com/ramimbo/mergework/pull/321",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )
        propose_treasury_action(
            session,
            action="close_bounty",
            payload={
                "bounty_id": close_bounty.id,
                "closed_by": "maintainer",
                "reference": "https://github.com/ramimbo/mergework/issues/322#close",
            },
            proposed_by="maintainer",
        )

        treasury_selects: list[str] = []

        def count_treasury_selects(
            conn,
            cursor,
            statement,
            parameters,
            context,
            executemany,
        ) -> None:
            del conn, cursor, parameters, context, executemany
            if "treasury_proposals" in statement.lower() and statement.lstrip().upper().startswith(
                "SELECT"
            ):
                treasury_selects.append(statement)

        bind = session.get_bind()
        event.listen(bind, "before_cursor_execute", count_treasury_selects)
        try:
            serialized = bounties_to_dict(
                [payout_bounty, close_bounty, plain_bounty], session=session
            )
        finally:
            event.remove(bind, "before_cursor_execute", count_treasury_selects)

    by_title = {bounty["title"]: bounty for bounty in serialized}
    assert by_title["Pending payout serializer"]["effective_awards_remaining"] == 1
    assert by_title["Pending close serializer"]["availability_state"] == "pending_close"
    assert by_title["Plain serializer"]["availability_state"] == "open"
    assert len(treasury_selects) == 1


def test_bounty_to_dict_narrows_single_pending_proposal_lookup(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        target_bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=331,
            issue_url="https://github.com/ramimbo/mergework/issues/331",
            title="Single bounty serializer",
            reward_mrwk="1",
            acceptance="Single serialization should only inspect matching proposals.",
        )
        other_bounty = target_bounty
        for issue_number in range(332, 341):
            other_bounty = create_bounty(
                session,
                repo="ramimbo/mergework",
                issue_number=issue_number,
                issue_url=f"https://github.com/ramimbo/mergework/issues/{issue_number}",
                title=f"Other serializer {issue_number}",
                reward_mrwk="1",
                acceptance="Used to guard against bounty_id prefix matches.",
            )
        assert other_bounty.id != target_bounty.id
        propose_treasury_action(
            session,
            action="pay_bounty",
            payload={
                "bounty_id": other_bounty.id,
                "to_account": "github:alice",
                "submission_url": "https://github.com/ramimbo/mergework/pull/340",
                "accepted_by": "maintainer",
            },
            proposed_by="maintainer",
        )

        treasury_selects: list[str] = []

        def count_treasury_selects(
            conn,
            cursor,
            statement,
            parameters,
            context,
            executemany,
        ) -> None:
            del conn, cursor, parameters, context, executemany
            if "treasury_proposals" in statement.lower() and statement.lstrip().upper().startswith(
                "SELECT"
            ):
                treasury_selects.append(statement)

        bind = session.get_bind()
        event.listen(bind, "before_cursor_execute", count_treasury_selects)
        try:
            serialized = bounty_to_dict(target_bounty, session=session)
        finally:
            event.remove(bind, "before_cursor_execute", count_treasury_selects)

    assert serialized["availability_state"] == "open"
    assert serialized["pending_payout_awards"] == 0
    assert serialized["effective_awards_remaining"] == 1
    assert len(treasury_selects) == 1
    assert "payload_json" in treasury_selects[0].lower()


def test_account_and_wallet_serializers_preserve_public_shapes(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Refactor public serializers",
            reward_mrwk="40",
            acceptance="Accepted work summaries stay stable after extraction.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:tatelyman",
            submission_url="https://github.com/ramimbo/mergework/pull/320",
            accepted_by="ramimbo",
            verifier_result={"label": "mrwk:accepted"},
        )
        wallet = register_wallet(session, public_key_hex="1" * 64, label="Serializer wallet")
        session.flush()
        bounty_row = session.get(Bounty, bounty.id)
        assert bounty_row is not None

        summary = account_accepted_summary(session, "github:tatelyman")
        accepted_work = accepted_work_for_account(session, "github:tatelyman")
        wallet_data = wallet_to_dict(session, wallet)

    assert summary["accepted_awards"] == 1
    assert summary["accepted_mrwk"] == "40"
    assert summary["latest_submission_url"] == "https://github.com/ramimbo/mergework/pull/320"
    assert summary["latest_proof_public_url"].startswith("https://mrwk.online/proofs/")
    assert accepted_work[0]["issue_url"] == "https://github.com/ramimbo/mergework/issues/320"
    assert accepted_work[0]["amount_mrwk"] == "40"
    assert accepted_work[0]["ledger_public_url"].startswith("https://mrwk.online/ledger/")
    assert accepted_work[0]["proof_public_url"].startswith("https://mrwk.online/proofs/")
    assert accepted_work[0]["bounty_public_url"].startswith("https://mrwk.online/bounties/")
    assert wallet_data["label"] == "Serializer wallet"
    assert wallet_data["balance_mrwk"] == "0"
    assert wallet_data["next_nonce"] == 1


def test_activity_serializer_fallbacks_keep_account_schema() -> None:
    assert (
        safe_account_accepted_summary(BrokenSession(), "github:alice") == empty_accepted_summary()
    )
    assert safe_accepted_work_for_account(BrokenSession(), "github:alice") == []


def test_activity_serializers_skip_malformed_public_proofs(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=320,
            issue_url="https://github.com/ramimbo/mergework/issues/320",
            title="Extract public serializers",
            reward_mrwk="40",
            acceptance="Malformed proof payloads should not break activity serializers.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:tatelyman",
            submission_url="https://github.com/ramimbo/mergework/pull/330",
            accepted_by="ramimbo",
            verifier_result={"label": "mrwk:accepted"},
        )
        proof_row = session.get(Proof, proof.hash)
        assert proof_row is not None
        proof_row.public_json = "{"

        activity = activity_to_dict(session)
        summary = account_accepted_summary(session, "github:tatelyman")
        accepted_work = accepted_work_for_account(session, "github:tatelyman")

    assert activity == {
        "totals": {"accepted_awards": 0, "accepted_mrwk": "0", "contributors": 0},
        "pending_totals": {"pending_awards": 0, "pending_mrwk": "0"},
        "query": "",
        "contributors": [],
        "pending_payouts": [],
        "recent": [],
    }
    assert summary == empty_accepted_summary()
    assert accepted_work == []


def test_bounty_award_serializer_uses_explicit_utc_timestamp(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Public bounty award history",
            reward_mrwk="40",
            acceptance="Award history timestamps should be explicit UTC.",
        )
        pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:tatelyman",
            submission_url="https://github.com/ramimbo/mergework/pull/321",
            accepted_by="ramimbo",
            verifier_result={"label": "mrwk:accepted"},
        )

        awards = bounty_awards_to_dict(session, bounty.id)

    assert len(awards) == 1
    assert awards[0]["created_at"].endswith("Z")


def test_bounty_award_serializer_skips_malformed_public_proofs(sqlite_url: str) -> None:
    create_schema(sqlite_url)
    with session_scope(sqlite_url) as session:
        ensure_genesis(session)
        bounty = create_bounty(
            session,
            repo="ramimbo/mergework",
            issue_number=321,
            issue_url="https://github.com/ramimbo/mergework/issues/321",
            title="Public bounty award history",
            reward_mrwk="40",
            acceptance="Malformed proof payloads should not break bounty award history.",
        )
        proof = pay_bounty(
            session,
            bounty_id=bounty.id,
            to_account="github:tatelyman",
            submission_url="https://github.com/ramimbo/mergework/pull/321",
            accepted_by="ramimbo",
            verifier_result={"label": "mrwk:accepted"},
        )
        proof_row = session.get(Proof, proof.hash)
        assert proof_row is not None
        proof_row.public_json = "{"

        awards = bounty_awards_to_dict(session, bounty.id)

    assert awards == []


def test_wallet_transfer_serializer_normalizes_aware_timestamp() -> None:
    transfer = WalletTransfer(
        hash="abc123",
        ledger_sequence=42,
        from_address="mrwk1from",
        to_address="mrwk1to",
        amount_microunits=1_500_000,
        nonce=7,
        memo="test transfer",
        created_at=datetime(2026, 5, 25, 12, 30, tzinfo=timezone(timedelta(hours=3))),
    )

    assert (
        wallet_transfer_to_dict(transfer)["created_at"]
        == datetime(2026, 5, 25, 9, 30, tzinfo=UTC).replace(tzinfo=None).isoformat()
    )
