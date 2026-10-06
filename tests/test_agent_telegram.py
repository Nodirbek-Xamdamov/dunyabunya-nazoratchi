"""Yordamchining Telegram qismi: rasm/hujjat bilan savol, albom, yaratilgan vazifa tugmalari,
"bajarildi" xabari va mas'ulga bildirishnoma.

Barcha testlar mok'langan — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import asyncio
import types

import pytest

import app.telegram.handlers as handlers
import app.telegram.handlers_manager as dm
from app.rbac import Role
from app.services.assistant import AssistantResult
from app.telegram import delivery


class _Bot:
    def __init__(self):
        self.sent, self.documents = [], []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return types.SimpleNamespace(message_id=len(self.sent))

    async def send_chat_action(self, **kwargs):
        pass

    async def send_voice(self, **kwargs):
        self.sent.append({"voice": True, **kwargs})


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _DmMessage:
    def __init__(self, *, text=None, caption=None, photo=None, document=None, media_group_id=None, uid=1):
        self.text, self.caption, self.photo, self.document = text, caption, photo, document
        self.voice, self.media_group_id = None, media_group_id
        self.from_user = types.SimpleNamespace(id=uid, username="ceo", full_name="Sardor Hasanxo'jayev")
        self.chat = types.SimpleNamespace(id=uid, type="private")
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


@pytest.fixture
def ceo_dm(monkeypatch):
    """CEO'ning shaxsiy chati: yordamchi va ilovalarni o'qish mok'langan."""
    state = types.SimpleNamespace(asked=[], results=[], extracted=[], blocks=[{"type": "image"}], problems=[])

    async def fake_ask(user_key, question, **kwargs):
        state.asked.append((question, kwargs))
        return state.results.pop(0) if state.results else AssistantResult(text="Javob.")

    async def fake_extract(messages, bot):
        state.extracted.append(list(messages))
        return list(state.blocks), list(state.problems)

    async def fake_send_result(bot, chat_id, thread_id, result, reply_markup=None):
        state.delivered = (chat_id, result, reply_markup)

    monkeypatch.setattr(dm, "_is_ceo", lambda uid, username=None: uid == 1)
    monkeypatch.setattr(dm, "ask_with_files", fake_ask)
    monkeypatch.setattr(dm.attachments, "extract", fake_extract)
    monkeypatch.setattr(dm.delivery, "send_result", fake_send_result)
    monkeypatch.setattr(dm, "_ALBUM_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(dm, "_albums", {})
    monkeypatch.setattr(dm, "_waiting_name", set())
    return state


# ── Rasm va hujjat bilan savol ──────────────────────────────────────────────

async def test_photo_with_caption_reaches_the_assistant_with_the_image(ceo_dm):
    message = _DmMessage(caption="bu fakturada summa qancha?", photo=[object()])
    await dm.on_private_dm_message(message, _Bot())

    question, kwargs = ceo_dm.asked[0]
    assert question == "bu fakturada summa qancha?" and kwargs["attachments"] == [{"type": "image"}]
    assert kwargs["role"] == Role.CEO and kwargs["user_name"] == "Sardor Hasanxo'jayev"
    assert kwargs["actor_id"] == 1 and kwargs["chat_id"] == 1


async def test_file_without_caption_is_described_instead_of_ignored(ceo_dm):
    await dm.on_private_dm_message(_DmMessage(document=object()), _Bot())
    assert "izohsiz" in ceo_dm.asked[0][0] and ceo_dm.asked[0][1]["attachments"]


async def test_album_is_answered_once_with_all_photos(ceo_dm):
    parts = [_DmMessage(caption="shularni solishtir", photo=[object()], media_group_id="g1"),
             _DmMessage(photo=[object()], media_group_id="g1"),
             _DmMessage(photo=[object()], media_group_id="g1")]
    await asyncio.gather(*[dm.on_private_dm_message(part, _Bot()) for part in parts])

    assert len(ceo_dm.asked) == 1 and ceo_dm.asked[0][0] == "shularni solishtir"
    assert ceo_dm.extracted == [parts] and dm._albums == {}


async def test_unreadable_file_is_explained_and_the_assistant_is_not_called(ceo_dm):
    ceo_dm.blocks, ceo_dm.problems = [], ["Bu turdagi faylni o'qiy olmayman."]
    message = _DmMessage(document=object(), caption="o'qib ber")
    await dm.on_private_dm_message(message, _Bot())
    assert message.answers == ["⚠️ Bu turdagi faylni o'qiy olmayman."] and ceo_dm.asked == []


async def test_plain_text_question_has_no_attachments(ceo_dm):
    await dm.on_private_dm_message(_DmMessage(text="sotuv qancha?"), _Bot())
    assert ceo_dm.asked[0][0] == "sotuv qancha?" and ceo_dm.asked[0][1]["attachments"] is None
    assert ceo_dm.extracted == []


async def test_stranger_sending_a_photo_is_not_served(ceo_dm, monkeypatch):
    async def no_manager(message):
        return None

    async def no_employee(uid):
        return None

    monkeypatch.setattr(dm, "_resolve_manager", no_manager)
    monkeypatch.setattr(dm.employee_store, "get_employee_by_telegram_id", no_employee)
    message = _DmMessage(photo=[object()], uid=999)
    await dm.on_private_dm_message(message, _Bot())
    assert ceo_dm.asked == [] and ceo_dm.extracted == [] and "/start" in message.answers[0]


# ── Yordamchi yaratgan vazifa ───────────────────────────────────────────────

async def test_answer_that_created_a_task_gets_open_and_delete_buttons(ceo_dm):
    ceo_dm.results = [AssistantResult(text="Vazifa yaratildi: #175", task_ids=[175])]
    await dm.on_private_dm_message(_DmMessage(text="Sardorga vazifa ber: omborni tekshirsin"), _Bot())

    _chat, _result, markup = ceo_dm.delivered
    buttons = markup.inline_keyboard[0]
    assert "id=175" in buttons[0].url and buttons[1].callback_data == "task_del:175"
    assert delivery.task_keyboard(AssistantResult(text="oddiy javob")) is None


async def test_assignee_notification_reports_why_it_could_not_be_sent(monkeypatch):
    registered = types.SimpleNamespace(telegram_user_id=555)
    people = {93: registered}
    sent = []

    async def by_user(odoo_user_id):
        return people.get(odoo_user_id)

    async def by_name(name):
        return registered if name == "Badalov Sardor" else None

    async def fake_send(bot, **kwargs):
        sent.append(kwargs)
        return kwargs["telegram_user_id"] == 555 and kwargs["title"] != "bloklangan"

    monkeypatch.setattr(handlers.employee_store, "get_employee_by_odoo_user_id", by_user)
    monkeypatch.setattr(handlers.employee_store, "find_active_employee_by_name", by_name)
    monkeypatch.setattr(handlers, "_send_task_to_employee", fake_send)
    common = dict(task_id=175, description="", topic_name="Ombor")

    assert await handlers.notify_task_assignee(_Bot(), title="Tekshirish", odoo_user_id=93, assignee_name="X", **common) == "yuborildi"
    # Odoo login hisobi yo'q xodim — botda ro'yxatdan o'tgan bo'lsa ismi bo'yicha topiladi
    assert await handlers.notify_task_assignee(_Bot(), title="Tekshirish", odoo_user_id=None,
                                               assignee_name="Badalov Sardor", **common) == "yuborildi"
    assert "ro'yxatdan o'tmagan" in await handlers.notify_task_assignee(
        _Bot(), title="Tekshirish", odoo_user_id=7, assignee_name="Notanish", **common)
    assert "bloklagan" in await handlers.notify_task_assignee(
        _Bot(), title="bloklangan", odoo_user_id=93, assignee_name="X", **common)


class _TaskMessage:
    text, caption, html_text = "Yangi vazifa", None, "Yangi vazifa"
    chat = types.SimpleNamespace(id=555)
    message_thread_id = None

    async def edit_text(self, *a, **kw):
        pass

    async def edit_reply_markup(self, **kw):
        pass


@pytest.mark.parametrize("chat_id, topic_id, expected", [
    (42, None, (42, None)),                 # yordamchi orqali shaxsiy chatdan berilgan — o'sha chatga
    ("group", 105, ("group", 105)),         # guruhdagi bo'lim mavzusidan berilgan — o'sha mavzuga
    ("group", None, None),                  # guruhning umumiy mavzusiga yozilmaydi
])
async def test_done_notice_goes_back_to_where_the_task_was_given(monkeypatch, chat_id, topic_id, expected):
    group = handlers.settings.TELEGRAM_GROUP_ID
    chat_id = group if chat_id == "group" else chat_id
    entry = types.SimpleNamespace(telegram_chat_id=chat_id, telegram_topic_id=topic_id, title="Omborni tekshirish",
                                  created_by_telegram_user_id=42)

    async def fake_log(session, task_id):
        return entry

    async def fake_stage(task_id, *, done):
        return None

    async def fake_employee(uid):
        return types.SimpleNamespace(full_name="Kadirov Sardor")

    async def answer(*a, **kw):
        return None

    monkeypatch.setattr(handlers, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(handlers.repo, "get_task_log_by_odoo_task_id", fake_log)
    monkeypatch.setattr(handlers.odoo, "update_task_stage", fake_stage)
    monkeypatch.setattr(handlers.employee_store, "get_employee_by_telegram_id", fake_employee)
    bot = _Bot()
    callback = types.SimpleNamespace(data="task_done:175", answer=answer, message=_TaskMessage(),
                                     from_user=types.SimpleNamespace(id=555, full_name="Sardor", username="s"))
    await handlers.on_task_done(callback, bot)

    if expected is None:
        assert bot.sent == []
    else:
        target = (group if expected[0] == "group" else expected[0], expected[1])
        assert [(m["chat_id"], m["message_thread_id"]) for m in bot.sent] == [target]
        assert "Omborni tekshirish — bajarildi (Kadirov Sardor)" in bot.sent[0]["text"]
