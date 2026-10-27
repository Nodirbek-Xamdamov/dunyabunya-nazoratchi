"""Tests run on the W0 test database ``genesis_test`` (synthetic data, shared/fixtures; SCHEMA.md §8a),
read through the ``w3_dashboard`` role like the dashboard itself. The real ``genesis`` database is never
used. The dashboard's own state database (layout changes) is a temporary file emptied before every test.

Fixtures are loaded by W0 (``python -E shared/fixtures/load_fixtures.py``); the numbers in the tests are
the ones that loader writes (2 days, 3 groups)."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).resolve().parent.parent
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

TEST_DATABASE = "genesis_test"
os.environ["W3_DATABASE"] = TEST_DATABASE  # never the real "genesis"
# W3-05: the tests' access log goes to a temporary file, never apps/w3-dashboard/logs/access.jsonl
os.environ["W3_ACCESS_LOG"] = os.path.join(tempfile.mkdtemp(prefix="w3-access-"), "access.jsonl")


def use_database() -> None:
    os.environ["W3_DATABASE"] = TEST_DATABASE
    from core.settings import load_settings

    load_settings.cache_clear()


@pytest.fixture(scope="session")
def state_db(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("state")
    path = folder / "state_test.db"
    os.environ["W3_STATE_DB"] = str(path)
    return path


@pytest.fixture(scope="session")
def demo_db(state_db) -> int:
    """The test database is reachable; returns the source id of the busiest test group."""
    use_database()
    from core import db
    from core.settings import load_settings

    with db.session(load_settings()) as session:
        busiest = session.scalar("busiest", "SELECT source_id FROM api.v_messages_public "
                                            "GROUP BY source_id ORDER BY COUNT(*) DESC, source_id LIMIT 1")
    assert busiest is not None, "genesis_test is empty: run shared/fixtures/load_fixtures.py"
    return int(busiest)


def clear_state(path: Path) -> None:
    if not path.exists():
        return
    conn = sqlite3.connect(path)
    try:
        for table in ("layout_versions", "chat_messages"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def demo_mode(demo_db, state_db):
    use_database()
    clear_state(state_db)
    yield
