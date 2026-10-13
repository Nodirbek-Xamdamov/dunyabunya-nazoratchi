import json
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

SessionFactory = async_sessionmaker[AsyncSession]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def create_engine(url: str, **kwargs: Any) -> AsyncEngine:
    """Async engine. SQLite gets WAL, foreign keys and write-locking transactions (see below)."""
    if url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"timeout": 30})
    engine = create_async_engine(url, json_serializer=_json_dumps, **kwargs)
    if engine.dialect.name == "sqlite":
        _configure_sqlite(engine)
    return engine


def _configure_sqlite(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection, _record) -> None:
        # We issue BEGIN ourselves (below); the driver's implicit, lazy BEGIN would start read
        # transactions that later fail to upgrade to write ("database is locked") instead of waiting.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        # FULL: a committed job survives power loss (NORMAL may drop the last commits in WAL mode). Volume is tiny.
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def _on_begin(connection) -> None:
        # Take the write lock up front: a transaction that starts also waits its turn (busy_timeout),
        # and check-then-write sequences (claiming a job, setting a document key) are race free.
        # Never hold a transaction open across network I/O.
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(engine, expire_on_commit=False)
