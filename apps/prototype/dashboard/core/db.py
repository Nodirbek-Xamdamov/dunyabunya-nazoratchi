"""Read-only access to the monitoring SQLite database.

* The file is opened through a ``file:...?mode=ro`` URI and ``PRAGMA query_only=ON`` is set, so
  nothing can be written even by mistake. Journal mode, indexes and the -wal/-shm files are never
  touched by this code.
* Every statement runs under a time limit (progress handler); a slow query is interrupted.
* A process-wide lock keeps at most one short connection to the bot's database open at a time.
* Fallback: if a direct read-only connection fails, a snapshot is copied with the SQLite backup
  API into dashboard/.cache/ (refreshed at most once a minute) and read from there.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from core.settings import DASHBOARD_DIR, Settings

log = logging.getLogger("dashboard.db")

_LOCK = threading.Lock()
_SNAPSHOT = DASHBOARD_DIR / ".cache" / "monitor_snapshot.db"
_SNAPSHOT_MAX_AGE = 60.0
_PROGRESS_STEPS = 20_000
_SLOW_LOG_SECONDS = 2.0


class DataError(Exception):
    """A data problem that is shown to the user as a friendly message."""


class DatabaseMissing(DataError):
    pass


class QueryTimeout(DataError):
    pass


def _connect_ro(path: Path, busy_ms: int) -> sqlite3.Connection:
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=busy_ms / 1000)
    conn.execute("PRAGMA query_only=ON")
    conn.execute(f"PRAGMA busy_timeout={int(busy_ms)}")
    return conn


def _snapshot(path: Path, busy_ms: int) -> Path:
    if _SNAPSHOT.is_file() and time.time() - _SNAPSHOT.stat().st_mtime < _SNAPSHOT_MAX_AGE:
        return _SNAPSHOT
    _SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    source = _connect_ro(path, busy_ms)
    try:
        target = sqlite3.connect(_SNAPSHOT)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return _SNAPSHOT


def open_connection(settings: Settings) -> sqlite3.Connection:
    path = settings.db_path
    if not path.is_file():
        raise DatabaseMissing(path.name)
    busy_ms = 3000
    try:
        conn = _connect_ro(path, busy_ms)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return conn
    except sqlite3.OperationalError as exc:
        log.warning("direct read-only connection failed (%s); using a snapshot copy", type(exc).__name__)
        return _connect_ro(_snapshot(path, busy_ms), busy_ms)


class Session:
    """One short read-only connection; every statement has its own time limit."""

    def __init__(self, conn: sqlite3.Connection, timeout: float, limit: int) -> None:
        self._conn = conn
        self._timeout = timeout
        self.limit = limit
        self._deadline = 0.0
        conn.set_progress_handler(self._check_deadline, _PROGRESS_STEPS)

    def _check_deadline(self) -> int:
        return 1 if time.monotonic() > self._deadline else 0

    def _execute(self, name: str, sql: str, params) -> sqlite3.Cursor:
        self._deadline = time.monotonic() + self._timeout
        started = time.monotonic()
        try:
            cursor = self._conn.execute(sql, tuple(params))
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                log.warning("query %s interrupted after %.1fs", name, time.monotonic() - started)
                raise QueryTimeout(name) from exc
            raise
        return cursor

    def rows(self, name: str, sql: str, params=(), limit: int | None = None) -> tuple[list[str], list[tuple], bool]:
        started = time.monotonic()
        cursor = self._execute(name, sql, params)
        try:
            if limit is None:
                data = cursor.fetchall()
                truncated = False
            else:
                data = cursor.fetchmany(limit + 1)
                truncated = len(data) > limit
                data = data[:limit]
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise QueryTimeout(name) from exc
            raise
        columns = [item[0] for item in cursor.description or ()]
        elapsed = time.monotonic() - started
        if elapsed > _SLOW_LOG_SECONDS:
            log.info("slow query %s: %.2fs, %d rows", name, elapsed, len(data))
        return columns, data, truncated

    def df(self, name: str, sql: str, params=(), limit: int | None = None) -> pd.DataFrame:
        columns, data, truncated = self.rows(name, sql, params, limit)
        frame = pd.DataFrame.from_records(data, columns=columns)
        frame.attrs["truncated"] = truncated
        return frame

    def one(self, name: str, sql: str, params=()) -> tuple | None:
        _columns, data, _truncated = self.rows(name, sql, params, limit=1)
        return data[0] if data else None

    def scalar(self, name: str, sql: str, params=(), default=None):
        row = self.one(name, sql, params)
        return default if row is None or row[0] is None else row[0]


@contextmanager
def session(settings: Settings):
    """``with session(settings) as s: s.df(...)`` -- one connection for a group of statements."""
    with _LOCK:
        conn = open_connection(settings)
        try:
            yield Session(conn, settings.query_timeout, settings.result_limit)
        finally:
            conn.close()
