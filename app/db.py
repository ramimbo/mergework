from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite:///") or database_url == "sqlite:///:memory:":
        return
    parsed = urlparse(database_url)
    raw_path = unquote(parsed.path)
    if len(raw_path) > 2 and raw_path[0] == "/" and raw_path[2] == ":":
        raw_path = raw_path[1:]
    if raw_path:
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)


def make_engine(database_url: str) -> Engine:
    _ensure_sqlite_parent(database_url)
    engine = create_engine(database_url, future=True)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_schema(database_url: str) -> None:
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    _migrate_schema(engine)
    engine.dispose()


def _migrate_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    if "bounties" not in inspector.get_table_names():
        return
    bounty_columns = {column["name"] for column in inspector.get_columns("bounties")}
    with engine.begin() as connection:
        if "max_awards" not in bounty_columns:
            connection.execute(
                text("ALTER TABLE bounties ADD COLUMN max_awards INTEGER NOT NULL DEFAULT 1")
            )
        if "awards_paid" not in bounty_columns:
            connection.execute(
                text("ALTER TABLE bounties ADD COLUMN awards_paid INTEGER NOT NULL DEFAULT 0")
            )
            connection.execute(text("UPDATE bounties SET awards_paid = 1 WHERE status = 'paid'"))
        if "submissions" in inspector.get_table_names():
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_submission_bounty_url "
                    "ON submissions (bounty_id, url)"
                )
            )


@contextmanager
def session_scope(database_url: str) -> Iterator[Session]:
    engine = make_engine(database_url)
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()
