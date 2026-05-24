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
    bounty_columns = {column["name"] for column in inspector.get_columns("bounties")}
    assert {"max_awards", "awards_paid"}.issubset(bounty_columns)

    submission_indexes = inspector.get_indexes("submissions")
    assert any(index["name"] == "uq_submission_bounty_url" for index in submission_indexes)
