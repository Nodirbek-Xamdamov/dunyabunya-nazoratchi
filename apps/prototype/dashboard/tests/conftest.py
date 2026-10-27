"""Tests run on a small demo database built in a temporary folder. The real monitoring database is
only ever opened read-only (test_real_smoke.py). The dashboard's own state database (layout changes,
chat history) is a temporary file emptied before every test, and no test can reach a real model API:
the keys are blanked here and agent tests use a scripted fake client."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).resolve().parent.parent
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

os.environ["DASHBOARD_ANTHROPIC_API_KEY"] = ""  # never the keys from dashboard\.env
os.environ["DASHBOARD_OPENAI_API_KEY"] = ""


def use_database(demo: bool, demo_path: Path | None = None) -> None:
    os.environ["DASHBOARD_DEMO_MODE"] = "1" if demo else "0"
    if demo_path is not None:
        os.environ["DASHBOARD_DEMO_DB_PATH"] = str(demo_path)
    os.environ["DASHBOARD_PASSWORD"] = ""
    from core.settings import load_settings

    load_settings.cache_clear()


@pytest.fixture(scope="session")
def state_db(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("state")
    path = folder / "state_test.db"
    os.environ["DASHBOARD_STATE_DB_PATH"] = str(path)
    return path


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory, state_db) -> Path:
    path = tmp_path_factory.mktemp("demo") / "demo_test.db"
    use_database(True, path)
    from demo.build_demo import build

    build(path, groups=24, days=21, seed=3, threads_per_day=3.0)
    return path


def clear_state(path: Path) -> None:
    if not path.exists():
        return
    conn = sqlite3.connect(path)
    try:
        for table in ("layout_versions", "chat_messages", "agent_prompts", "custom_agents", "agent_models", "connections",
                      "bot_offsets", "bot_messages"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def demo_mode(demo_db, state_db):
    use_database(True, demo_db)
    clear_state(state_db)
    yield
