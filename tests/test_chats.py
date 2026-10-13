import json
from datetime import datetime, timedelta, timezone

import pytest

from docbot.telegram.chats import ChatRegistry
from docbot.telegram.handlers import membership_change


def test_env_chats_are_always_served(tmp_path):
    chats = ChatRegistry(tmp_path / "chats.json", [8001])
    assert chats.allowed(8001) and not chats.allowed(-100)
    assert chats.add(8001, title="me", added_by=1) is False  # nothing to store


def test_registered_groups_survive_a_restart(tmp_path):
    path = tmp_path / "chats.json"
    chats = ChatRegistry(path, [])
    assert chats.add(-100, title="Ombor", added_by=8001)
    assert ChatRegistry(path, []).allowed(-100)
    assert json.loads(path.read_text(encoding="utf-8"))["-100"]["title"] == "Ombor"
    assert chats.remove(-100) and not ChatRegistry(path, []).allowed(-100)


def test_a_group_that_becomes_a_supergroup_keeps_being_served(tmp_path):
    chats = ChatRegistry(tmp_path / "chats.json", [])
    chats.add(-100, title="Ombor", added_by=8001)
    chats.migrate(-100, -1001234567890)
    assert chats.allowed(-1001234567890)
    chats.migrate(-555, -1005555555555)  # an unknown group stays unknown
    assert not chats.allowed(-1005555555555)


def test_only_messages_written_after_the_connection_are_new(tmp_path):
    chats = ChatRegistry(tmp_path / "chats.json", [8001])
    chats.add(-100, title="Ombor", added_by=8001)
    connected = chats.connected_at(-100)
    assert connected is not None and connected.tzinfo is not None
    assert not chats.is_new(-100, connected - timedelta(minutes=5))  # posted before the bot came
    assert chats.is_new(-100, connected + timedelta(seconds=1))
    assert chats.is_new(8001, datetime(2020, 1, 1, tzinfo=timezone.utc))  # ALLOWED_CHAT_IDS: no date to compare
    chats.migrate(-100, -1001234567890)
    assert chats.connected_at(-1001234567890) == connected  # the supergroup keeps the connection time


def test_a_paused_group_is_not_served_and_goes_live_with_only_new_messages(tmp_path, monkeypatch):
    from docbot.telegram import chats as chats_module

    t0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
    now = {"t": t0}
    monkeypatch.setattr(chats_module, "utcnow", lambda: now["t"])
    chats = ChatRegistry(tmp_path / "chats.json", [8001])
    chats.add(-100, title="Ombor", added_by=8001)  # 09:00
    assert chats.pause_all() == ["Ombor"]
    assert chats.allowed(-100) and chats.paused(-100) and not chats.live(-100)
    assert chats.live(8001)  # ALLOWED_CHAT_IDS / private chats are never paused
    now["t"] = t0 + timedelta(minutes=30)
    chats.go_live()  # 09:30 "tayyor"
    assert chats.live(-100)
    assert not chats.is_new(-100, t0 + timedelta(minutes=10))  # written while paused: stays untouched
    assert chats.is_new(-100, t0 + timedelta(minutes=31))


def test_the_running_bot_sees_changes_made_by_the_cli(tmp_path):
    bot = ChatRegistry(tmp_path / "chats.json", [])
    bot.add(-100, title="Ombor", added_by=8001)
    cli = ChatRegistry(tmp_path / "chats.json", [])
    import os, time
    cli.pause_all()
    stamp = time.time() + 5
    os.utime(tmp_path / "chats.json", (stamp, stamp))  # make sure the change is visible even on coarse clocks
    assert bot.paused(-100)


def test_a_broken_file_does_not_stop_the_bot(tmp_path):
    (tmp_path / "chats.json").write_text("{broken", encoding="utf-8")
    assert not ChatRegistry(tmp_path / "chats.json", []).allowed(-100)


@pytest.mark.parametrize(
    ("old", "new", "change"),
    [
        ("left", "member", "added"),
        ("kicked", "administrator", "added"),
        ("member", "administrator", "promoted"),
        ("administrator", "member", "demoted"),
        ("member", "left", "removed"),
        ("administrator", "kicked", "removed"),
        ("member", "member", None),
    ],
)
def test_what_happened_to_the_bot_in_a_chat(old, new, change):
    assert membership_change(old, new) == change
