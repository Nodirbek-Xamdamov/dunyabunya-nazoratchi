"""app/services/memory_digest.py uchun testlar — kunlik/haftalik fayl-asosli
xotira: guruh xabarlarini OpenAI bilan xulosalash, CLAUDE.md/WEEKLY_MEMORY.md'ga
yozish, xom xabarlarni bazadan tozalash, va rejalashtiruvchi vaqt hisob-kitobi.

Barcha testlar mok'langan OpenAI javoblari va vaqtinchalik fayllar bilan ishlaydi —
haqiqiy tarmoqqa yoki haqiqiy bazaga chiqilmaydi.
"""
from __future__ import annotations

import types
from datetime import datetime, time, timedelta

import pytest

import app.services.memory_digest as memory_digest


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _fake_session_local():
    return _FakeSession()


class _FakeOpenAIClient:
    def __init__(self, fn):
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=fn)
        )


def _fake_openai_response(text: str):
    message = types.SimpleNamespace(content=text)
    choice = types.SimpleNamespace(message=message)
    return types.SimpleNamespace(choices=[choice])


def _patch_openai_text(monkeypatch, text: str):
    async def fake_create(*, model, messages, max_completion_tokens):
        return _fake_openai_response(text)

    monkeypatch.setattr(memory_digest, "_get_openai_client", lambda: _FakeOpenAIClient(fake_create))


def _patch_openai_failure(monkeypatch, exc: Exception):
    async def fake_create(*, model, messages, max_completion_tokens):
        raise exc

    monkeypatch.setattr(memory_digest, "_get_openai_client", lambda: _FakeOpenAIClient(fake_create))


@pytest.fixture(autouse=True)
def _isolated_memory_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_digest, "MEMORY_DIR", tmp_path)
    monkeypatch.setattr(memory_digest, "DAILY_MEMORY_PATH", tmp_path / "CLAUDE.md")
    monkeypatch.setattr(memory_digest, "WEEKLY_MEMORY_PATH", tmp_path / "WEEKLY_MEMORY.md")
    monkeypatch.setattr(memory_digest, "SessionLocal", _fake_session_local)
    yield


class _FakeMsg:
    def __init__(self, name: str, text: str, hour: int = 10):
        self.from_user_name = name
        self.text = text
        self.created_at = datetime(2026, 9, 8, hour, 0)


# ── run_daily_digest ─────────────────────────────────────────────────────────

async def test_daily_digest_no_messages_returns_none_and_writes_nothing(monkeypatch):
    async def fake_get_all(session, chat_id):
        return []

    called = {"delete": False}

    async def fake_delete(session, chat_id):
        called["delete"] = True
        return 0

    monkeypatch.setattr(memory_digest, "get_all_group_messages", fake_get_all)
    monkeypatch.setattr(memory_digest, "delete_group_messages", fake_delete)

    result = await memory_digest.run_daily_digest(chat_id=-100)
    assert result is None
    assert called["delete"] is False
    assert not memory_digest.DAILY_MEMORY_PATH.exists()


async def test_daily_digest_summarizes_writes_file_and_clears_messages(monkeypatch):
    messages = [
        _FakeMsg("Nodir", "Ombor tekshirildi", hour=9),
        _FakeMsg("Aziz", "Sotuvda muammo bor", hour=14),
    ]

    async def fake_get_all(session, chat_id):
        assert chat_id == -100
        return messages

    deleted = {}

    async def fake_delete(session, chat_id):
        deleted["chat_id"] = chat_id
        return len(messages)

    monkeypatch.setattr(memory_digest, "get_all_group_messages", fake_get_all)
    monkeypatch.setattr(memory_digest, "delete_group_messages", fake_delete)
    _patch_openai_text(monkeypatch, "Bugun ombor tekshirildi, sotuvda muammo aniqlandi.")

    result = await memory_digest.run_daily_digest(chat_id=-100)

    assert result == "Bugun ombor tekshirildi, sotuvda muammo aniqlandi."
    assert deleted["chat_id"] == -100
    content = memory_digest.DAILY_MEMORY_PATH.read_text(encoding="utf-8")
    assert "Bugun ombor tekshirildi" in content
    assert "[09:00] Nodir" not in content  # xulosa yoziladi, xom xabar emas


async def test_daily_digest_openai_failure_still_writes_fallback_text(monkeypatch):
    """OpenAI xato bersa ham kun butunlay yo'qolmasligi kerak — zaxira matn yoziladi."""
    async def fake_get_all(session, chat_id):
        return [_FakeMsg("Nodir", "Salom")]

    async def fake_delete(session, chat_id):
        return 1

    monkeypatch.setattr(memory_digest, "get_all_group_messages", fake_get_all)
    monkeypatch.setattr(memory_digest, "delete_group_messages", fake_delete)
    _patch_openai_failure(monkeypatch, RuntimeError("OpenAI 500"))

    result = await memory_digest.run_daily_digest(chat_id=-100)
    assert "tayyorlab bo'lmadi" in result
    assert memory_digest.DAILY_MEMORY_PATH.exists()


# ── run_weekly_digest ─────────────────────────────────────────────────────────

async def test_weekly_digest_no_daily_entries_returns_none():
    result = await memory_digest.run_weekly_digest()
    assert result is None
    assert not memory_digest.WEEKLY_MEMORY_PATH.exists()


async def test_weekly_digest_combines_last_7_daily_entries(monkeypatch):
    # 9 ta kunlik yozuv yozamiz — faqat oxirgi 7 tasi hisobga olinishi kerak.
    entries = [f"## 2026-09-{d:02d}\n\nKun {d} xulosasi" for d in range(1, 10)]
    memory_digest.DAILY_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    memory_digest.DAILY_MEMORY_PATH.write_text(memory_digest._ENTRY_SEP.join(entries), encoding="utf-8")

    captured = {}

    async def fake_create(*, model, messages, max_completion_tokens):
        captured["messages"] = messages
        return _fake_openai_response("Haftalik xulosa matni.")

    monkeypatch.setattr(memory_digest, "_get_openai_client", lambda: _FakeOpenAIClient(fake_create))

    result = await memory_digest.run_weekly_digest()

    user_content = captured["messages"][1]["content"]
    assert result == "Haftalik xulosa matni."
    assert "Kun 1 xulosasi" not in user_content  # eng eskisi tashlab yuborilgan
    assert "Kun 9 xulosasi" in user_content
    content = memory_digest.WEEKLY_MEMORY_PATH.read_text(encoding="utf-8")
    assert "Haftalik xulosa matni." in content


# ── _next_run (rejalashtiruvchi vaqt hisob-kitobi) ───────────────────────────

def test_next_run_daily_returns_future_time():
    target = memory_digest._next_run(time(hour=0, minute=10))
    assert target > datetime.now(memory_digest._TZ)
    assert target.hour == 0 and target.minute == 10


def test_next_run_weekly_lands_on_correct_weekday():
    target = memory_digest._next_run(time(hour=0, minute=20), weekday=0)  # dushanba
    assert target.weekday() == 0
    assert target > datetime.now(memory_digest._TZ)
