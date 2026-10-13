"""Vazifa tugmalari: "Ha, topshirish" (CEO, guruhda) va "Bajarildi"/"Bekor qilindi"
(xodim, shaxsiy chatda).

2026-10-05 da topilgan xatolar:
- kutilayotgan tanlov TOPIK bo'yicha saqlanardi: bitta topikdagi ikkinchi vazifa
  birinchisini bosib ketar va birinchi xabar ostidagi "Ha" bosilsa ikkinchi vazifa
  birinchi xodimga yozilardi;
- vazifa ovozli xabar (caption) bo'lib ketgani uchun "Bajarildi" bosilganda xabar
  yangilanmas, tugmalar qolaverardi;
- Odoo xatosi haqidagi ogohlantirish xodimga ko'rinmasdi (callback'ga ikkinchi javob).

Barcha testlar mok'langan — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import types

import pytest

import app.telegram.handlers as handlers
from app.services.llm import AnalyzeResult


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return types.SimpleNamespace(message_id=len(self.sent))

    async def send_chat_action(self, **kwargs):
        pass


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _user(uid=1, username="ceo", full_name="CEO"):
    return types.SimpleNamespace(id=uid, username=username, full_name=full_name)


def _topic_message(message_id: int, thread_id: int = 5):
    return types.SimpleNamespace(
        text="vazifa", caption=None, voice=None, message_id=message_id, message_thread_id=thread_id,
        chat=types.SimpleNamespace(id=handlers.settings.TELEGRAM_GROUP_ID), from_user=_user(),
    )


class _ButtonMessage:
    """Tugmali xabar: matnli (text) yoki ovozli (caption)."""

    def __init__(self, *, text=None, caption=None, thread_id=None):
        self.text, self.caption = text, caption
        self.chat = types.SimpleNamespace(id=1)
        self.message_thread_id = thread_id
        self.edits = []

    async def edit_text(self, text, **kw):
        self.edits.append(("text", text, kw))

    async def edit_caption(self, **kw):
        self.edits.append(("caption", kw.get("caption"), kw))

    async def edit_reply_markup(self, **kw):
        self.edits.append(("markup", None, kw))


def _callback(data: str, message=None, uid=1):
    answers = []

    async def answer(*a, **kw):
        answers.append((a, kw))

    return types.SimpleNamespace(data=data, from_user=_user(uid), answer=answer, answers=answers,
                                 message=message or _ButtonMessage(text="Topshirasizmi?", thread_id=5))


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USER_ID", 1)
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USERNAME", "")
    monkeypatch.setattr(handlers, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(handlers, "_pending_task_selections", {})


def _patch_topic_flow(monkeypatch, results: list[AnalyzeResult], candidates: dict[str, list[dict]]):
    queue = list(results)

    async def fake_text(message, bot):
        return "vazifa"

    async def fake_topic(session, thread_id):
        return types.SimpleNamespace(odoo_project_id=28, name="Sotuv")

    async def fake_save_group_message(*a, **kw):
        return None

    async def fake_analyze(text):
        return queue.pop(0)

    async def fake_find(name):
        return candidates[name]

    monkeypatch.setattr(handlers, "get_message_text", fake_text)
    monkeypatch.setattr(handlers.repo, "get_topic_by_telegram_id", fake_topic)
    monkeypatch.setattr(handlers, "save_group_message", fake_save_group_message)
    monkeypatch.setattr(handlers, "analyze_message", fake_analyze)
    monkeypatch.setattr(handlers, "_find_odoo_users", fake_find)


def _task(title: str, assignee: str) -> AnalyzeResult:
    return AnalyzeResult(is_task=True, title=title, description=title, assignee_name=assignee)


def _button_data(sent: dict) -> list[str]:
    return [b.callback_data for row in sent["reply_markup"].inline_keyboard for b in row]


# ── "Ha, topshirish" ────────────────────────────────────────────────────────

async def test_two_tasks_in_one_topic_keep_separate_confirmations(monkeypatch):
    _patch_topic_flow(
        monkeypatch,
        [_task("Hisobot tayyorlash", "Aziz"), _task("Omborni sanash", "Bobur")],
        {"Aziz": [{"id": 10, "name": "Aziz Karimov", "score": 100.0, "source": "res.users"}],
         "Bobur": [{"id": 20, "name": "Bobur Aliyev", "score": 100.0, "source": "res.users"}]},
    )
    created = []

    async def fake_create_task(**kw):
        created.append(kw)
        return 500 + len(created)

    async def fake_save_task_log(session, **kw):
        return None

    async def nothing(*a, **kw):
        return None

    monkeypatch.setattr(handlers.odoo, "create_task", fake_create_task)
    monkeypatch.setattr(handlers.repo, "save_task_log", fake_save_task_log)
    monkeypatch.setattr(handlers, "_notify_assignee_or_warn", nothing)
    monkeypatch.setattr(handlers, "_notify_general", nothing)

    bot = _FakeBot()
    await handlers.on_topic_message(_topic_message(101), bot)
    await handlers.on_topic_message(_topic_message(102), bot)  # shu topikda ikkinchi vazifa

    first_yes, second_yes = _button_data(bot.sent[0])[0], _button_data(bot.sent[1])[0]
    assert first_yes == "task_assign:101:10" and second_yes == "task_assign:102:20"

    # BIRINCHI xabar ostidagi "Ha" — birinchi vazifa, birinchi xodim
    await handlers.on_task_assign(_callback(first_yes), bot)
    assert created == [dict(project_id=28, name="Hisobot tayyorlash", description="Hisobot tayyorlash",
                            user_ids=[10], date_deadline=None)]
    # Ikkinchisi o'z joyida kutib turibdi
    await handlers.on_task_assign(_callback(second_yes), bot)
    assert created[1]["name"] == "Omborni sanash" and created[1]["user_ids"] == [20]


async def test_assign_button_for_someone_not_offered_writes_nothing(monkeypatch):
    """Tugmadagi ID shu vazifa uchun taklif qilinganlar orasida bo'lmasa — vazifa yozilmaydi
    (ilgari bunday ID res.users deb olinib, begona odamga biriktirilardi)."""
    handlers._pending_task_selections[101] = {
        "result": _task("Hisobot", "Aziz"), "topic": types.SimpleNamespace(odoo_project_id=28, name="Sotuv"),
        "users": [{"id": 10, "name": "Aziz Karimov", "source": "res.users"}],
        "reply_thread_id": 5, "telegram_topic_id": 5, "telegram_message_id": 101,
    }

    async def fail_create_task(**kw):
        raise AssertionError("vazifa yaratilmasligi kerak edi")

    monkeypatch.setattr(handlers.odoo, "create_task", fail_create_task)
    callback = _callback("task_assign:101:999")
    await handlers.on_task_assign(callback, _FakeBot())

    assert "Muddati o'tgan" in callback.message.edits[0][1]
    assert 101 in handlers._pending_task_selections


async def test_task_log_failure_does_not_stop_the_assignee_notification(monkeypatch):
    handlers._pending_task_selections[101] = {
        "result": AnalyzeResult(is_task=True, title="", description="Sarlavhasiz vazifa", assignee_name="Aziz"),
        "topic": types.SimpleNamespace(odoo_project_id=28, name="Sotuv"),
        "users": [{"id": 10, "name": "Aziz <K>", "source": "res.users"}],
        "reply_thread_id": 5, "telegram_topic_id": 5, "telegram_message_id": 101,
    }
    notified = []

    async def fake_create_task(**kw):
        return 600

    async def broken_save_task_log(session, **kw):
        raise RuntimeError("baza vaqtincha ishlamayapti")

    async def fake_notify(bot, **kw):
        notified.append(kw)

    async def nothing(*a, **kw):
        return None

    monkeypatch.setattr(handlers.odoo, "create_task", fake_create_task)
    monkeypatch.setattr(handlers.repo, "save_task_log", broken_save_task_log)
    monkeypatch.setattr(handlers, "_notify_assignee_or_warn", fake_notify)
    monkeypatch.setattr(handlers, "_notify_general", nothing)

    callback = _callback("task_assign:101:10")
    await handlers.on_task_assign(callback, _FakeBot())

    assert notified and notified[0]["title"] == "Sarlavhasiz vazifa"  # bo'sh sarlavha o'rniga tavsif
    # Ism HTML uchun ekranlangan (aks holda Telegram xabarni rad etardi)
    assert "Aziz &lt;K&gt;" in callback.message.edits[0][1]


# ── "Bajarildi" / "Bekor qilindi" ───────────────────────────────────────────

def _patch_finish(monkeypatch, *, fail: Exception | None = None, topic_id: int | None = 7):
    stages = []

    async def fake_update(task_id, *, done):
        if fail:
            raise fail
        stages.append((task_id, done))

    async def fake_log(session, task_id):
        return types.SimpleNamespace(telegram_chat_id=-100123, telegram_topic_id=topic_id, title="Omborni sanash")

    async def fake_employee(uid):
        return types.SimpleNamespace(full_name="Bobur Aliyev")

    monkeypatch.setattr(handlers.odoo, "update_task_stage", fake_update)
    monkeypatch.setattr(handlers.repo, "get_task_log_by_odoo_task_id", fake_log)
    monkeypatch.setattr(handlers.employee_store, "get_employee_by_telegram_id", fake_employee)
    return stages


async def test_done_on_a_voice_task_edits_the_caption_and_removes_buttons(monkeypatch):
    stages = _patch_finish(monkeypatch)
    message = _ButtonMessage(caption="Yangi vazifa: narx < 5 mln & tez")
    bot = _FakeBot()
    await handlers.on_task_done(_callback("task_done:55", message, uid=42), bot)

    assert stages == [(55, True)]
    kind, text, kw = message.edits[0]
    assert kind == "caption" and kw["reply_markup"] is None and kw["parse_mode"] == "HTML"
    assert text == "Yangi vazifa: narx &lt; 5 mln &amp; tez\n\n✅ <b>Bajarildi deb belgilandi</b>"
    assert bot.sent == [dict(chat_id=-100123, message_thread_id=7,
                             text="✅ Omborni sanash — bajarildi (Bobur Aliyev)")]


async def test_cancel_on_a_text_task_edits_the_text(monkeypatch):
    stages = _patch_finish(monkeypatch)
    message = _ButtonMessage(text="Yangi vazifa")
    bot = _FakeBot()
    await handlers.on_task_cancel(_callback("task_cancel:56", message, uid=42), bot)

    assert stages == [(56, False)]
    assert message.edits[0][:2] == ("text", "Yangi vazifa\n\n❌ <b>Bekor qilindi deb belgilandi</b>")
    assert bot.sent[0]["text"] == "❌ Omborni sanash — bekor qilindi (Bobur Aliyev)"


async def test_done_when_odoo_fails_tells_the_employee_and_keeps_buttons(monkeypatch):
    _patch_finish(monkeypatch, fail=RuntimeError("Odoo 30s ichida javob bermadi"))
    message = _ButtonMessage(caption="Yangi vazifa")
    bot = _FakeBot()
    callback = _callback("task_done:55", message, uid=42)
    await handlers.on_task_done(callback, bot)

    assert message.edits == []  # tugmalar qoladi — qayta bosish mumkin
    assert len(callback.answers) == 1  # callback'ga faqat bir marta javob beriladi
    assert bot.sent[0]["chat_id"] == 42 and "Odoo 30s ichida javob bermadi" in bot.sent[0]["text"]


async def test_task_notification_escapes_html_in_title(monkeypatch):
    async def no_speech(text):
        return b""

    monkeypatch.setattr(handlers, "synthesize_speech", no_speech)
    bot = _FakeBot()
    sent = await handlers._send_task_to_employee(
        bot, task_id=1, title="Narx < 5 mln & sifat", description="A<B", topic_name="Sotuv",
        telegram_user_id=42)

    assert sent is True
    assert "Narx &lt; 5 mln &amp; sifat" in bot.sent[0]["text"] and "A&lt;B" in bot.sent[0]["text"]


async def test_topic_message_with_failed_analysis_writes_no_task(monkeypatch):
    """AI xizmatlari javob bermasa xabar vazifami-yo'qmi noma'lum — Odoo'ga hech narsa
    yozilmaydi va CEO'ga aniq xabar beriladi (ilgari "salom" ham vazifa bo'lib yozilardi)."""
    async def fake_text(message, bot):
        return "salom"

    async def fake_topic(session, thread_id):
        return types.SimpleNamespace(odoo_project_id=28, name="Sotuv")

    async def fake_save(*a, **kw):
        return None

    async def fake_analyze(text):
        return AnalyzeResult(is_task=True, is_task_confidence=0.7, title=text, description=text,
                             analysis_failed=True)

    async def fail_write(*a, **kw):
        raise AssertionError("vazifa yozilmasligi kerak edi")

    monkeypatch.setattr(handlers, "get_message_text", fake_text)
    monkeypatch.setattr(handlers.repo, "get_topic_by_telegram_id", fake_topic)
    monkeypatch.setattr(handlers, "save_group_message", fake_save)
    monkeypatch.setattr(handlers, "analyze_message", fake_analyze)
    monkeypatch.setattr(handlers, "_write_task", fail_write)
    monkeypatch.setattr(handlers, "_assistant_reply", fail_write)

    bot = _FakeBot()
    await handlers.on_topic_message(_topic_message(101), bot)

    assert len(bot.sent) == 1 and "tahlil qilib bo'lmadi" in bot.sent[0]["text"]