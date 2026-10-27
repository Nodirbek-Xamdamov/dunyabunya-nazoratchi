"""Read-only access to the Genesis PostgreSQL database (W3-02).

* The dashboard connects as the ``w3_dashboard`` role (SCHEMA.md §3). The password is read from
  secrets/postgres.env through the shared loader (genesis_common.read_secret_env) only while a connection
  is opened; it is never stored in the settings, logged or shown.
* Every connection is read-only on two levels: the role has no write rights on the ``api`` views, and the
  session itself runs with ``default_transaction_read_only = on`` (an INSERT/UPDATE fails even where the
  role could write, e.g. ``core.audit_log``). ``search_path`` is ``api`` only: the dashboard reads the
  W0 views, never the ``core`` tables.
* Only SELECT/WITH statements are accepted by ``Session`` (checked before anything is sent).
* Every statement runs under ``statement_timeout``; a slow query is cancelled by the server.
* Paging: ``Session.df(..., limit=N, offset=M)`` fetches one page (N rows + one to know if more follow).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import contextmanager

import pandas as pd
import psycopg
from genesis_common import find_root, load_config, read_secret_env
from psycopg import errors as pg_errors

from core.settings import APP_NAME, DASHBOARD_DIR, Settings

log = logging.getLogger("dashboard.db")

_LOCK = threading.Lock()
_SLOW_LOG_SECONDS = 2.0
_READ_ONLY_SQL = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
PASSWORD_KEY = "W3_DASHBOARD_PASSWORD"
SECRET_FILE = "postgres.env"


class DataError(Exception):
    """A data problem that is shown to the user as a friendly message."""


class DatabaseMissing(DataError):
    pass


class QueryTimeout(DataError):
    pass


class WriteRefused(DataError):
    """A statement that is not a plain read was given to the read-only session."""


def _password() -> str:
    cfg = load_config(APP_NAME, root=find_root(DASHBOARD_DIR))
    try:
        return read_secret_env(SECRET_FILE, cfg)[PASSWORD_KEY]
    except KeyError as exc:  # only the key name is reported
        raise DatabaseMissing(f"{SECRET_FILE}: {PASSWORD_KEY} yo'q") from exc


def open_connection(settings: Settings) -> psycopg.Connection:
    db = settings.db
    timeout_ms = int(settings.query_timeout * 1000)
    options = (f"-c default_transaction_read_only=on -c statement_timeout={timeout_ms} "
               f"-c search_path=api -c TimeZone=UTC")
    try:
        return psycopg.connect(host=db.host, port=db.port, dbname=db.database, user=db.role,
                               password=_password(), options=options, connect_timeout=5, autocommit=True)
    except psycopg.OperationalError as exc:
        log.warning("cannot connect to %s as %s (%s)", db.label, db.role, type(exc).__name__)
        raise DatabaseMissing(db.label) from exc


class Session:
    """One short read-only connection; every statement has the server-side time limit."""

    def __init__(self, conn: psycopg.Connection, timeout: float, limit: int) -> None:
        self._conn = conn
        self._timeout = timeout
        self.limit = limit

    def _execute(self, name: str, sql: str, params) -> psycopg.Cursor:
        if not _READ_ONLY_SQL.match(sql):
            log.warning("query %s refused: not a SELECT", name)
            raise WriteRefused(name)
        try:
            return self._conn.execute(sql, tuple(params))
        except pg_errors.QueryCanceled as exc:
            log.warning("query %s cancelled after %.0fs", name, self._timeout)
            raise QueryTimeout(name) from exc
        except pg_errors.ReadOnlySqlTransaction as exc:
            raise WriteRefused(name) from exc
        except psycopg.Error as exc:
            log.warning("query %s failed: %s", name, type(exc).__name__)
            raise DataError(name) from exc

    def rows(self, name: str, sql: str, params=(), limit: int | None = None,
             offset: int = 0) -> tuple[list[str], list[tuple], bool]:
        started = time.monotonic()
        if limit is not None:
            sql = f"{sql}\nLIMIT %s OFFSET %s"
            params = (*params, int(limit) + 1, max(0, int(offset)))
        cursor = self._execute(name, sql, params)
        data = cursor.fetchall()
        truncated = limit is not None and len(data) > limit
        if truncated:
            data = data[:limit]
        columns = [item.name for item in cursor.description or ()]
        elapsed = time.monotonic() - started
        if elapsed > _SLOW_LOG_SECONDS:
            log.info("slow query %s: %.2fs, %d rows", name, elapsed, len(data))
        return columns, data, truncated

    def df(self, name: str, sql: str, params=(), limit: int | None = None, offset: int = 0) -> pd.DataFrame:
        columns, data, truncated = self.rows(name, sql, params, limit, offset)
        frame = pd.DataFrame.from_records(data, columns=columns)
        frame.attrs["truncated"] = truncated
        frame.attrs["offset"] = offset
        return frame

    def one(self, name: str, sql: str, params=()) -> tuple | None:
        """First row only (the statement may carry its own LIMIT, so none is added)."""
        return self._execute(name, sql, params).fetchone()

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
