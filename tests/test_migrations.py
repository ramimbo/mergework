from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_head_builds_deploy_schema(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "mergework.sqlite3"
    monkeypatch.setenv("MERGEWORK_DATABASE_URL", f"sqlite:///{database_path}")

    config = Config("alembic.ini")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    inspector = inspect(engine)

    assert "bounties" in inspector.get_table_names()
    assert "treasury_proposals" in inspector.get_table_names()
    assert "treasury_challenges" in inspector.get_table_names()
    bounty_columns = {column["name"] for column in inspector.get_columns("bounties")}
    assert {"max_awards", "awards_paid"}.issubset(bounty_columns)
    proposal_columns = {column["name"] for column in inspector.get_columns("treasury_proposals")}
    assert {
        "action",
        "status",
        "payload_json",
        "payload_hash",
        "proposed_at",
        "executes_after",
    }.issubset(proposal_columns)
    challenge_columns = {column["name"] for column in inspector.get_columns("treasury_challenges")}
    assert {"proposal_id", "challenger_account", "challenge_type", "status"}.issubset(
        challenge_columns
    )

    submission_indexes = inspector.get_indexes("submissions")
    assert any(index["name"] == "uq_submission_bounty_url" for index in submission_indexes)
    proposal_indexes = inspector.get_indexes("treasury_proposals")
    assert any(index["name"] == "ix_treasury_proposals_payload_hash" for index in proposal_indexes)
    proposal_fks = inspector.get_foreign_keys("treasury_proposals")
    assert any(
        fk["constrained_columns"] == ["executed_ledger_sequence"]
        and fk["referred_table"] == "ledger_entries"
        and fk["referred_columns"] == ["sequence"]
        for fk in proposal_fks
    )
    challenge_fks = inspector.get_foreign_keys("treasury_challenges")
    assert any(
        fk["constrained_columns"] == ["proposal_id"]
        and fk["referred_table"] == "treasury_proposals"
        for fk in challenge_fks
    )
