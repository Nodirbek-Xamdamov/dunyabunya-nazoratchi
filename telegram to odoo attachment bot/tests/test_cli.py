import shutil
import sqlite3
from pathlib import Path

import pytest
from helpers import VALID_ENV

from docbot.cli import main
from docbot.config import EnvSettings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in EnvSettings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.delenv("DOCBOT_HOME", raising=False)


@pytest.fixture
def home(tmp_path) -> Path:
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    shutil.copy(PROJECT_ROOT / ".env.example", tmp_path / ".env")
    return tmp_path


def run(home: Path, *args: str) -> int:
    return main(["--home", str(home), *args])


def test_check_config_lists_what_is_still_a_placeholder(home, capsys):
    assert run(home, "check-config") == 0  # a template is a valid config, just not a runnable one
    out = capsys.readouterr().out
    assert "to'ldirish kerak" in out
    assert "BOT_TOKEN" in out
    assert "OPENAI_API_KEY" in out
    assert "ALLOWED_CHAT_IDS" in out


def test_check_config_strict_fails_while_placeholders_remain(home):
    assert run(home, "check-config", "--strict") == 1


def test_check_config_passes_for_a_filled_in_config(home, capsys):
    (home / ".env").write_text(VALID_ENV + "OPENAI_API_KEY=sk-test-0123456789abcdefghij\n", encoding="utf-8")
    assert run(home, "check-config", "--strict") == 0
    assert "tayyor" in capsys.readouterr().out


def test_check_config_reports_every_problem_and_never_a_secret_value(home, capsys):
    (home / ".env").write_text("BOT_TOKEN=123456789:AAHdummyTokenForTestsOnly_0123456789abcd\nADMIN_IDS=oops\n", encoding="utf-8")
    assert run(home, "check-config") == 1
    out = capsys.readouterr().out
    assert "XATO" in out
    assert "ODOO_URL" in out and "ODOO_API_KEY" in out and "ADMIN_IDS" in out
    assert "AAHdummyToken" not in out


def test_migrate_creates_the_database_and_is_repeatable(home, capsys):
    assert run(home, "migrate") == 0
    assert run(home, "migrate") == 0
    assert "revision" in capsys.readouterr().out
    db = home / "data" / "docbot.db"
    assert db.is_file()
    assert (home / "data" / "images").is_dir()
    assert (home / "data" / "logs" / "docbot.log").is_file()
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"jobs", "audit_log"} <= tables
    assert "employees" not in tables  # dropped in 0004: files are attached whoever sends them


def test_migrate_stops_on_a_broken_config(home, capsys):
    (home / "config" / "settings.yaml").write_text("queue:\n  workers: many\n", encoding="utf-8")
    assert run(home, "migrate") == 1
    assert not (home / "data" / "docbot.db").exists()
    assert "queue.workers" in capsys.readouterr().out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert "docbot" in capsys.readouterr().out
