"""P1-6 + P1-4: app/services/task_reminders.py — muddati o'tgan/eskirgan
vazifalar haqida CEOga (P1-6) va mas'ulsiz qolgan vazifalar haqida General
mavzuga (P1-4) kunlik ertalabki eslatma. Barcha testlar mok'langan
Odoo/DB/Bot bilan ishlaydi — tarmoqqa chiqilmaydi."""
from __future__ import annotations

import types
from datetime import date, datetime, time, timedelta

import pytest

import app.services.task_reminders as task_reminders
from app import ceo_state


class _FakeTaskLog:
    def __init__(self, odoo_task_id):
        self.odoo_task_id = odoo_task_id


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _fake_session_local():
    return _FakeSession()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(task_reminders, "SessionLocal", _fake_session_local)
    monkeypatch.setattr(ceo_state, "_PATH", tmp_path / "ceo_state.json")
    ceo_state._cached_id = None
    ceo_state._loaded = False
    yield
    ceo_state._cached_id = None
    ceo_state._loaded = False


def _today_str(days_offset: int = 0) -> str:
    return (date.today() + timedelta(days=days_offset)).isoformat() + " 00:00:00"


# ─── _find_overdue_tasks ─────────────────────────────────────────────────

async def test_task_with_passed_deadline_and_open_stage_is_overdue(monkeypatch):
    async def fake_get_recent(session, **kw):
        return [_FakeTaskLog(1)]

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task":
            return [{"id": 1, "name": "Omborni tekshirish", "date_deadline": _today_str(-2)[:10],
                      "create_date": _today_str(-5), "stage_id": [10, "Jarayonda"],
                      "user_ids": [], "project_id": [5, "Ombor"]}]
        return []

    monkeypatch.setattr(task_reminders, "get_recent_task_logs", fake_get_recent)
    monkeypatch.setattr(task_reminders._odoo, "execute_kw", fake_execute_kw)

    result = await task_reminders._find_overdue_tasks()
    assert len(result) == 1
    assert result[0]["title"] == "Omborni tekshirish"
    assert result[0]["department"] == "Ombor"
    assert result[0]["deadline"] == date.today() - timedelta(days=2)


async def test_task_with_future_deadline_is_not_overdue(monkeypatch):
    async def fake_get_recent(session, **kw):
        return [_FakeTaskLog(1)]

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task":
            return [{"id": 1, "name": "X", "date_deadline": _today_str(5)[:10],
                      "create_date": _today_str(-1), "stage_id": [10, "Jarayonda"],
                      "user_ids": [], "project_id": [5, "Ombor"]}]
        return []

    monkeypatch.setattr(task_reminders, "get_recent_task_logs", fake_get_recent)
    monkeypatch.setattr(task_reminders._odoo, "execute_kw", fake_execute_kw)

    assert await task_reminders._find_overdue_tasks() == []


async def test_done_stage_task_never_overdue_even_with_passed_deadline(monkeypatch):
    async def fake_get_recent(session, **kw):
        return [_FakeTaskLog(1)]

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task":
            return [{"id": 1, "name": "X", "date_deadline": _today_str(-10)[:10],
                      "create_date": _today_str(-15), "stage_id": [20, "Bajarildi"],
                      "user_ids": [], "project_id": [5, "Ombor"]}]
        return []

    monkeypatch.setattr(task_reminders, "get_recent_task_logs", fake_get_recent)
    monkeypatch.setattr(task_reminders._odoo, "execute_kw", fake_execute_kw)

    assert await task_reminders._find_overdue_tasks() == []


async def test_cancelled_stage_task_never_overdue(monkeypatch):
    async def fake_get_recent(session, **kw):
        return [_FakeTaskLog(1)]

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task":
            return [{"id": 1, "name": "X", "date_deadline": _today_str(-10)[:10],
                      "create_date": _today_str(-15), "stage_id": [21, "Bekor qilindi"],
                      "user_ids": [], "project_id": [5, "Ombor"]}]
        return []

    monkeypatch.setattr(task_reminders, "get_recent_task_logs", fake_get_recent)
    monkeypatch.setattr(task_reminders._odoo, "execute_kw", fake_execute_kw)

    assert await task_reminders._find_overdue_tasks() == []


async def test_no_deadline_task_becomes_stale_after_threshold_days(monkeypatch):
    monkeypatch.setattr(task_reminders.settings, "TASK_REMINDER_STALE_DAYS", 3)

    async def fake_get_recent(session, **kw):
        return [_FakeTaskLog(1), _FakeTaskLog(2)]

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task":
            return [
                {"id": 1, "name": "Eski (4 kun)", "date_deadline": False, "create_date": _today_str(-4),
                 "stage_id": [10, "Jarayonda"], "user_ids": [], "project_id": [5, "Ombor"]},
                {"id": 2, "name": "Yangi (1 kun)", "date_deadline": False, "create_date": _today_str(-1),
                 "stage_id": [10, "Jarayonda"], "user_ids": [], "project_id": [5, "Ombor"]},
            ]
        return []

    monkeypatch.setattr(task_reminders, "get_recent_task_logs", fake_get_recent)
    monkeypatch.setattr(task_reminders._odoo, "execute_kw", fake_execute_kw)

    result = await task_reminders._find_overdue_tasks()
    assert len(result) == 1
    assert result[0]["title"] == "Eski (4 kun)"
    assert result[0]["days_since_created"] == 4


async def test_assignee_names_resolved_via_batched_res_users_lookup(monkeypatch):
    async def fake_get_recent(session, **kw):
        return [_FakeTaskLog(1)]

    calls = {"res_users_ids": None}

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task":
            return [{"id": 1, "name": "X", "date_deadline": _today_str(-1)[:10],
                      "create_date": _today_str(-5), "stage_id": [10, "Jarayonda"],
                      "user_ids": [7, 8], "project_id": [5, "Sotuv"]}]
        if model == "res.users":
            calls["res_users_ids"] = args[0][0][2]
            return [{"id": 7, "name": "Nodir Aliyev"}, {"id": 8, "name": "Aziz Karimov"}]
        return []

    monkeypatch.setattr(task_reminders, "get_recent_task_logs", fake_get_recent)
    monkeypatch.setattr(task_reminders._odoo, "execute_kw", fake_execute_kw)

    result = await task_reminders._find_overdue_tasks()
    assert result[0]["assignee_names"] == ["Nodir Aliyev", "Aziz Karimov"]
    assert set(calls["res_users_ids"]) == {7, 8}


async def test_no_task_logs_short_circuits_without_odoo_call(monkeypatch):
    async def fake_get_recent(session, **kw):
        return []

    async def fake_execute_kw(model, method, args, kwargs=None):
        raise AssertionError("Odoo'ga chiqilmasligi kerak edi")

    monkeypatch.setattr(task_reminders, "get_recent_task_logs", fake_get_recent)
    monkeypatch.setattr(task_reminders._odoo, "execute_kw", fake_execute_kw)

    assert await task_reminders._find_overdue_tasks() == []


# ─── _format_reminder_message ───────────────────────────────────────────

def test_format_message_with_deadline_and_assignee():
    tasks = [{
        "task_id": 1, "title": "Omborni tekshirish", "department": "Ombor",
        "assignee_names": ["Murod Aliyev"], "deadline": date(2026, 9, 20), "days_since_created": None,
    }]
    msg = task_reminders._format_reminder_message(tasks)
    assert "Murod Aliyev" in msg
    assert "Omborni tekshirish" in msg
    assert "2026-09-20" in msg
    assert "Ombor" in msg


def test_format_message_without_deadline_uses_days_since_created():
    tasks = [{
        "task_id": 2, "title": "Hisobot tayyorlash", "department": "Moliya",
        "assignee_names": [], "deadline": None, "days_since_created": 5,
    }]
    msg = task_reminders._format_reminder_message(tasks)
    assert "5 kun" in msg
    assert "mas'ulsiz" in msg


def test_format_message_lists_multiple_tasks_in_one_message():
    tasks = [
        {"task_id": 1, "title": "A", "department": "", "assignee_names": ["X"], "deadline": date(2026, 1, 1), "days_since_created": None},
        {"task_id": 2, "title": "B", "department": "", "assignee_names": ["Y"], "deadline": date(2026, 1, 2), "days_since_created": None},
    ]
    msg = task_reminders._format_reminder_message(tasks)
    assert msg.count("📌") == 2
    assert "A" in msg and "B" in msg


# ─── run_task_reminder_check ─────────────────────────────────────────────

class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


async def test_no_overdue_tasks_sends_nothing(monkeypatch):
    async def fake_find(*a, **kw):
        return []

    monkeypatch.setattr(task_reminders, "_find_overdue_tasks", fake_find)
    bot = _FakeBot()
    result = await task_reminders.run_task_reminder_check(bot)
    assert result is None
    assert bot.sent == []


async def test_overdue_tasks_but_ceo_id_unknown_sends_nothing(monkeypatch):
    async def fake_find(*a, **kw):
        return [{"task_id": 1, "title": "X", "department": "", "assignee_names": [], "deadline": date.today(), "days_since_created": None}]

    monkeypatch.setattr(task_reminders, "_find_overdue_tasks", fake_find)
    monkeypatch.setattr(task_reminders.settings, "CEO_TELEGRAM_USER_ID", 0)
    bot = _FakeBot()
    result = await task_reminders.run_task_reminder_check(bot)
    assert result is None
    assert bot.sent == []


async def test_overdue_tasks_with_known_ceo_id_sends_message(monkeypatch):
    async def fake_find(*a, **kw):
        return [{"task_id": 1, "title": "Omborni tekshirish", "department": "Ombor",
                  "assignee_names": ["Murod"], "deadline": date.today() - timedelta(days=1), "days_since_created": None}]

    monkeypatch.setattr(task_reminders, "_find_overdue_tasks", fake_find)
    await ceo_state.remember_ceo_telegram_user_id(999)

    bot = _FakeBot()
    result = await task_reminders.run_task_reminder_check(bot)

    assert result is not None
    assert "Omborni tekshirish" in result
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == 999


# ─── _find_unassigned_tasks (P1-4) ───────────────────────────────────────

async def test_unassigned_open_task_is_included(monkeypatch):
    async def fake_get_recent(session, **kw):
        return [_FakeTaskLog(1)]

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task":
            return [{"id": 1, "name": "Mas'ulsiz vazifa", "date_deadline": False,
                      "create_date": _today_str(-1), "stage_id": [10, "Jarayonda"],
                      "user_ids": [], "project_id": [5, "Sotuv"]}]
        return []

    monkeypatch.setattr(task_reminders, "get_recent_task_logs", fake_get_recent)
    monkeypatch.setattr(task_reminders._odoo, "execute_kw", fake_execute_kw)

    result = await task_reminders._find_unassigned_tasks()
    assert result == [{"task_id": 1, "title": "Mas'ulsiz vazifa", "department": "Sotuv"}]


async def test_assigned_open_task_is_excluded(monkeypatch):
    async def fake_get_recent(session, **kw):
        return [_FakeTaskLog(1)]

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task":
            return [{"id": 1, "name": "Biriktirilgan", "date_deadline": False,
                      "create_date": _today_str(-1), "stage_id": [10, "Jarayonda"],
                      "user_ids": [7], "project_id": [5, "Sotuv"]}]
        return []

    monkeypatch.setattr(task_reminders, "get_recent_task_logs", fake_get_recent)
    monkeypatch.setattr(task_reminders._odoo, "execute_kw", fake_execute_kw)

    assert await task_reminders._find_unassigned_tasks() == []


async def test_done_unassigned_task_is_excluded(monkeypatch):
    """Mas'ulsiz bo'lsa ham, allaqachon 'Bajarildi' bosqichida bo'lsa endi ahamiyatsiz."""
    async def fake_get_recent(session, **kw):
        return [_FakeTaskLog(1)]

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task":
            return [{"id": 1, "name": "X", "date_deadline": False,
                      "create_date": _today_str(-1), "stage_id": [20, "Bajarildi"],
                      "user_ids": [], "project_id": [5, "Sotuv"]}]
        return []

    monkeypatch.setattr(task_reminders, "get_recent_task_logs", fake_get_recent)
    monkeypatch.setattr(task_reminders._odoo, "execute_kw", fake_execute_kw)

    assert await task_reminders._find_unassigned_tasks() == []


def test_format_unassigned_message_lists_all_tasks():
    tasks = [
        {"task_id": 1, "title": "A", "department": "Sotuv"},
        {"task_id": 2, "title": "B", "department": ""},
    ]
    msg = task_reminders._format_unassigned_message(tasks)
    assert "2 ta" in msg
    assert "A" in msg and "Sotuv" in msg
    assert "B" in msg


async def test_run_unassigned_task_digest_sends_to_general_topic(monkeypatch):
    async def fake_find(*a, **kw):
        return [{"task_id": 1, "title": "Mas'ulsiz", "department": "Sotuv"}]

    monkeypatch.setattr(task_reminders, "_find_unassigned_tasks", fake_find)
    bot = _FakeBot()

    result = await task_reminders.run_unassigned_task_digest(bot)

    assert result is not None
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == task_reminders.settings.TELEGRAM_GROUP_ID
    assert bot.sent[0]["message_thread_id"] is None


async def test_run_unassigned_task_digest_sends_nothing_when_empty(monkeypatch):
    async def fake_find(*a, **kw):
        return []

    monkeypatch.setattr(task_reminders, "_find_unassigned_tasks", fake_find)
    bot = _FakeBot()

    result = await task_reminders.run_unassigned_task_digest(bot)
    assert result is None
    assert bot.sent == []


# ─── _next_run ───────────────────────────────────────────────────────────

def test_next_run_returns_future_time_at_correct_hour():
    target = task_reminders._next_run(time(hour=9, minute=0))
    assert target > datetime.now(task_reminders._TZ)
    assert target.hour == 9 and target.minute == 0
