"""Ishonchlilik: polling, /health, throttling xabari, xato ogohlantirishlari (P1-2),
topik-loyiha mosligi va kunlik eslatmalar.

Barcha testlar mok'langan — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import asyncio
import logging
import time
import types

import pytest
from aiogram.types import Chat, Message, Update, User

import app.alerts as alerts
import app.main as main_module
import app.rate_limiter as rate_limiter
import app.services.task_reminders as task_reminders
import app.services.topic_check as topic_check


# ── Polling ─────────────────────────────────────────────────────────────────

class _PollingBot:
    """get_me bir necha marta yiqiladi (tarmoq hali tayyor emas), keyin update'lar keladi."""

    def __init__(self, get_me_failures: int, batches: list):
        self.get_me_failures = get_me_failures
        self.batches = list(batches)
        self.offsets = []

    async def get_me(self):
        if self.get_me_failures:
            self.get_me_failures -= 1
            raise RuntimeError("Telegram'ga yetib bo'lmadi")
        return types.SimpleNamespace(username="dunyabunya_agent_bot")

    async def get_updates(self, *, offset, timeout, allowed_updates):
        self.offsets.append(offset)
        if not self.batches:
            raise asyncio.CancelledError
        return self.batches.pop(0)


async def test_polling_survives_startup_failure_and_does_not_drop_the_backlog(monkeypatch):
    """Ilgari: (1) get_me tsikldan tashqarida edi — ishga tushishda Telegram'ga yetib bo'lmasa
    polling butunlay o'lib, /health esa "ok" deb turaverardi; (2) offset=-1 bilan boshlanib,
    bot o'chib turganida kelgan xabarlarning oxirgisidan boshqa hammasi tashlab yuborilardi."""
    fake_bot = _PollingBot(2, [[Update(update_id=10), Update(update_id=11)], []])
    spawned = []

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(main_module, "bot", fake_bot)
    monkeypatch.setattr(main_module, "_spawn_update", lambda u: spawned.append(u.update_id))
    monkeypatch.setattr(main_module.asyncio, "sleep", no_sleep)

    with pytest.raises(asyncio.CancelledError):
        await main_module._parallel_polling()

    assert spawned == [10, 11]
    assert fake_bot.offsets == [None, 12, 12]  # navbat boshidan; keyin oxirgisidan keyingisi


def _health(monkeypatch, *, task, last_attempt_age, last_ok_age):
    now = time.monotonic()
    monkeypatch.setattr(main_module, "_polling", {
        "task": task, "last_attempt": now - last_attempt_age,
        "last_ok": (now - last_ok_age) if last_ok_age is not None else 0.0,
    })
    return main_module._polling_health()


def test_health_reports_dead_or_hung_polling_as_unhealthy(monkeypatch):
    alive = types.SimpleNamespace(done=lambda: False)
    dead = types.SimpleNamespace(done=lambda: True)

    assert _health(monkeypatch, task=alive, last_attempt_age=5, last_ok_age=5) == (
        True, {"polling": "ok", "seconds_since_last_poll": 5})
    assert _health(monkeypatch, task=dead, last_attempt_age=5, last_ok_age=5)[0] is False
    assert _health(monkeypatch, task=None, last_attempt_age=5, last_ok_age=5)[0] is False
    healthy, info = _health(monkeypatch, task=alive, last_attempt_age=600, last_ok_age=600)
    assert healthy is False and info["polling"] == "stalled"
    # Tarmoq yo'q, lekin urinishlar davom etyapti — qayta ishga tushirish foyda bermaydi
    healthy, info = _health(monkeypatch, task=alive, last_attempt_age=2, last_ok_age=900)
    assert healthy is True and info["polling"] == "telegram unreachable"


async def test_health_endpoint_returns_503_when_polling_stopped(monkeypatch):
    monkeypatch.setattr(main_module, "_polling", {"task": None, "last_attempt": 0.0, "last_ok": 0.0})
    response = await main_module.health()
    assert response.status_code == 503 and b'"unhealthy"' in response.body


# ── Throttling ──────────────────────────────────────────────────────────────

def _message_update(update_id: int, user_id: int, username: str | None, *, age_seconds: float = 0) -> Update:
    user = User(id=user_id, is_bot=False, first_name="Test", username=username)
    message = Message(message_id=update_id, date=int(time.time() - age_seconds),
                      chat=Chat(id=user_id, type="private"), from_user=user, text="vazifa")
    return Update(update_id=update_id, message=message)


async def test_throttled_ceo_is_told_once_that_messages_were_dropped(monkeypatch):
    """Limit oshganda CEO'ning xabari izsiz yo'qolmasligi kerak (ilgari jimgina tashlanardi)."""
    sent = []

    async def fake_send_message(**kwargs):
        sent.append(kwargs)

    async def fail_feed(bot, update):
        raise AssertionError("limitdan oshgan xabar qayta ishlanmasligi kerak")

    monkeypatch.setattr(main_module, "bot", types.SimpleNamespace(send_message=fake_send_message))
    monkeypatch.setattr(main_module.dp, "feed_update", fail_feed)
    monkeypatch.setattr(main_module, "is_rate_limited", lambda uid: True)
    monkeypatch.setattr(rate_limiter, "_last_notice", {})
    monkeypatch.setattr(main_module.ceo_state.settings, "CEO_TELEGRAM_USER_ID", 7001)
    monkeypatch.setattr(main_module.ceo_state.settings, "CEO_TELEGRAM_USERNAME", "")

    await main_module._run_update(_message_update(1, 7001, "ceo"))
    await main_module._run_update(_message_update(2, 7001, "ceo"))
    await main_module._run_update(_message_update(3, 555, "oddiy_xodim"))  # CEO emas — javobsiz

    assert len(sent) == 1 and sent[0]["chat_id"] == 7001 and "qabul qilinmadi" in sent[0]["text"]


async def test_backlog_messages_are_not_throttled(monkeypatch):
    """Bot o'chib turganida yig'ilgan (eski) xabarlar jonli toshqin emas — hammasi qayta ishlanadi."""
    handled = []

    async def fake_feed(bot, update):
        handled.append(update.update_id)

    monkeypatch.setattr(main_module.dp, "feed_update", fake_feed)
    monkeypatch.setattr(main_module, "is_rate_limited", lambda uid: True)

    await main_module._run_update(_message_update(1, 555, None, age_seconds=3600))
    assert handled == [1]


# ── P1-2: xato ogohlantirishlari ────────────────────────────────────────────

def _record(message: str, level=logging.ERROR, name="app.services.assistant", exc=None):
    exc_info = (type(exc), exc, None) if exc else None
    return logging.LogRecord(name, level, __file__, 1, message, None, exc_info)


def test_alert_handler_batches_errors_and_suppresses_repeats():
    handler = alerts.TelegramAlertHandler()
    handler.emit(_record("tool export_excel error", exc=RuntimeError("Odoo 30s ichida javob bermadi")))
    handler.emit(_record("tool export_excel error", exc=RuntimeError("Odoo 30s ichida javob bermadi")))
    handler.emit(_record("analyze_message: OpenAI ham, Claude ham ishlamadi"))
    handler.emit(_record("yuborib bo'lmadi", name=alerts.__name__))  # o'z xatosi — qayta kirmaydi

    message = handler.drain()
    assert message.count("•") == 2
    assert "Odoo 30s ichida javob bermadi" in message and "OpenAI ham, Claude ham" in message
    assert handler.drain() is None
    assert handler.level == logging.ERROR  # WARNING/INFO ogohlantirish bo'lmaydi


def test_alerts_are_off_without_alert_chat_id(monkeypatch):
    monkeypatch.setattr(alerts.settings, "ALERT_CHAT_ID", 0)
    assert alerts.start_alerts(bot=object()) is None


# ── Topik -> Odoo loyiha mosligi ────────────────────────────────────────────

class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _topics(*rows):
    async def fake_list_topics(session):
        return [types.SimpleNamespace(telegram_topic_id=tid, name=name, odoo_project_id=pid)
                for tid, name, pid in rows]
    return fake_list_topics


async def test_swapped_topic_projects_are_detected_with_the_right_target(monkeypatch):
    """2026-10-05: "Sotuv" topiki "Moliya" loyihasiga, "Moliya" topiki "Sotuv" loyihasiga ulangan edi."""
    async def fake_execute_kw(model, method, args, kwargs=None):
        return [{"id": 27, "name": "Moliya"}, {"id": 28, "name": "Sotuv"}, {"id": 31, "name": "Ombor"}]

    monkeypatch.setattr(topic_check, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(topic_check.repo, "list_topics",
                        _topics((2, "Sotuv", 27), (3, "Moliya", 28), (6, "Ombor", 31), (1, "General", 28)))
    monkeypatch.setattr(topic_check._odoo, "execute_kw", fake_execute_kw)

    mismatches = await topic_check.find_topic_mismatches()
    assert [(m["name"], m["odoo_project_name"], m["correct_project_id"]) for m in mismatches] == [
        ("Sotuv", "Moliya", 28), ("Moliya", "Sotuv", 27)]


async def test_topic_pointing_to_a_missing_project_is_reported(monkeypatch, caplog):
    async def fake_execute_kw(model, method, args, kwargs=None):
        return []  # Odoo almashgan — eski raqamli loyihalar yo'q

    monkeypatch.setattr(topic_check, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(topic_check.repo, "list_topics", _topics((9, "HR", 39)))
    monkeypatch.setattr(topic_check._odoo, "execute_kw", fake_execute_kw)

    with caplog.at_level("ERROR", logger="app.services.topic_check"):
        await topic_check.verify_topic_projects(delay_seconds=0)
    assert "'HR' topiki mavjud bo'lmagan #39 loyihaga ulangan" in caplog.text


# ── Kunlik eslatmalar ───────────────────────────────────────────────────────

def _patch_reminder_data(monkeypatch, logs, rows):
    async def fake_get_recent(session, **kw):
        return logs

    async def fake_execute_kw(model, method, args, kwargs=None):
        return rows if model == "project.task" else []

    monkeypatch.setattr(task_reminders, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(task_reminders, "get_recent_task_logs", fake_get_recent)
    monkeypatch.setattr(task_reminders._odoo, "execute_kw", fake_execute_kw)


def _task_row(task_id, project, **extra):
    return {"id": task_id, "name": f"Vazifa {task_id}", "date_deadline": "2020-01-01",
            "create_date": "2020-01-01 00:00:00", "stage_id": [10, "Jarayonda"], "user_ids": [],
            "project_id": project, **extra}


async def test_reminder_ignores_task_ids_left_over_from_an_old_odoo_instance(monkeypatch):
    """task_log'dagi #187 eski Odoo'da bot yaratgan vazifa edi; yangi bazada shu raqam boshqa
    loyihadagi begona vazifaga to'g'ri keladi — u CEO'ga eslatilmasligi kerak."""
    logs = [types.SimpleNamespace(odoo_task_id=300, odoo_project_id=28),
            types.SimpleNamespace(odoo_task_id=187, odoo_project_id=39)]
    _patch_reminder_data(monkeypatch, logs, [
        _task_row(300, [28, "Sotuv"]),
        _task_row(187, [18, "TASK IT"]),
    ])
    overdue = await task_reminders._find_overdue_tasks()
    assert [t["task_id"] for t in overdue] == [300]


async def test_reminder_skips_tasks_closed_by_state_in_odoo(monkeypatch):
    """Vazifa Odoo'da bosqichi o'zgartirilmasdan "Done" qilib yopilgan bo'lishi mumkin."""
    logs = [types.SimpleNamespace(odoo_task_id=1, odoo_project_id=28),
            types.SimpleNamespace(odoo_task_id=2, odoo_project_id=28)]
    _patch_reminder_data(monkeypatch, logs, [
        _task_row(1, [28, "Sotuv"], state="1_done"),
        _task_row(2, [28, "Sotuv"], state="01_in_progress"),
    ])
    assert [t["task_id"] for t in await task_reminders._find_overdue_tasks()] == [2]
    assert [t["task_id"] for t in await task_reminders._find_unassigned_tasks()] == [2]


async def test_daily_checks_read_odoo_once_and_split_long_messages(monkeypatch):
    logs = [types.SimpleNamespace(odoo_task_id=i, odoo_project_id=28) for i in range(1, 121)]
    task_calls = []

    async def fake_get_recent(session, **kw):
        return logs

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task":
            task_calls.append(1)
            return [_task_row(i, [28, "Sotuv"], name="Juda uzun nomli vazifa " * 3 + str(i)) for i in range(1, 121)]
        return []

    async def fake_ceo_ids():
        return [999]

    sent = []

    async def fake_send_message(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(task_reminders, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(task_reminders, "get_recent_task_logs", fake_get_recent)
    monkeypatch.setattr(task_reminders._odoo, "execute_kw", fake_execute_kw)
    monkeypatch.setattr(task_reminders.ceo_state, "get_ceo_telegram_user_ids", fake_ceo_ids)

    await task_reminders.run_daily_checks(types.SimpleNamespace(send_message=fake_send_message))

    assert len(task_calls) == 1  # ikkala hisobot uchun bitta Odoo so'rovi
    ceo_parts = [m for m in sent if m["chat_id"] == 999]
    assert len(ceo_parts) > 1 and all(len(m["text"]) <= 4096 for m in sent)
    assert sum(m["text"].count("📌") for m in ceo_parts) == 120  # hech bir vazifa tushib qolmagan
