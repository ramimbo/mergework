from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.db import create_schema, session_scope
from app.ledger.service import create_bounty, ensure_genesis, pay_bounty, register_wallet
from app.models import Bounty, Proof, WalletTransfer
from app.serializers import (
    accepted_work_for_account,
    account_accepted_summary,
    activity_to_dict,
    bounty_awards_to_dict,
    bounty_list_summary,
    bounty_to_dict,
    empty_accepted_summary,
    safe_accepted_work_for_account,
    safe_account_accepted_summary,
    wallet_to_dict,
    wallet_transfer_to_dict,
)


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
    assert bounty_list_summary([bounty_data]) == {
        "bounties_shown": 1,
        "open_awards": 4,
        "open_pool_mrwk": "100",
    }


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
    assert accepted_work[0]["issue_url"] == "https://github.com/ramimbo/mergework/issues/320"
    assert accepted_work[0]["amount_mrwk"] == "40"
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
        "query": "",
        "contributors": [],
        "recent": [],
    }
    assert summary == empty_accepted_summary()
    assert accepted_work == []


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
