"""app.bot.handlers.group_monitor -- guruh xabarlarini yig'ish: har bir xabar darhol monitoring
bazasiga yoziladi, bot guruhga HECH NARSA yozmaydi va modelni kutmaydi."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler

from app import monitor_classifier, monitor_db
from app.bot.handlers import chat_events, group_monitor
from app.bot.handlers.group_monitor import collect
from app.monitor_db import Group, Message
from tests.monitor_helpers import all_rows

GROUP_ID = -1002222222222
REPORT_CHAT = -1009990000001
ADMIN_ID, MANAGER_ID, CUSTOMER_ID = 100, 101, 200
T0 = datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(group_monitor.settings, "MONITOR_ENABLED", True)
    monkeypatch.setattr(group_monitor.settings, "REPORT_CHAT_ID", REPORT_CHAT)
    monkeypatch.setattr(group_monitor.settings, "GROUP_MONITOR_EXCLUDED_GROUP_IDS", "")


def _user(user_id, username=None, full_name="Ism", is_bot=False):
    return SimpleNamespace(id=user_id, username=username, full_name=full_name, is_bot=is_bot)


def _admin(user, title=None):
    return SimpleNamespace(user=user, custom_title=title, status="administrator")


CUSTOMER = _user(CUSTOMER_ID, "mijoz1", "Vali Mijoz")
ADMIN = _user(ADMIN_ID, "ali_staff", "Ali Xodim")
MANAGER = _user(MANAGER_ID, "menejer_bek", "Bek Menejer")


def _make_bot():
    """Har qanday yozish urinishi ko'rinib turadigan bot: faqat adminlar ro'yxatini bera oladi."""
    bot = MagicMock()
    bot.get_chat_administrators = AsyncMock(return_value=[_admin(ADMIN), _admin(MANAGER, "Menejer")])
    for method in ("send_message", "send_document", "copy_message", "forward_message", "set_message_reaction"):
        setattr(bot, method, AsyncMock())
    return bot


def _message(bot, text, user=CUSTOMER, *, chat_id=GROUP_ID, message_id=1, date=T0, title="Mijoz guruhi", **extra):
    fields = dict(
        bot=bot, text=text, caption=None, content_type="text", from_user=user, sender_chat=None,
        chat=SimpleNamespace(id=chat_id, title=title, type="supergroup"), message_id=message_id, date=date,
        reply_to_message=None, edit_date=None, author_signature=None,
        voice=None, video_note=None, video=None, audio=None, animation=None, sticker=None, document=None, photo=None,
    )
    return SimpleNamespace(**(fields | extra))


async def _messages():
    return await all_rows(Message)


def _nothing_written(bot) -> bool:
    return not any(
        getattr(bot, method).await_count
        for method in ("send_message", "send_document", "copy_message", "forward_message", "set_message_reaction")
    )


async def test_customer_message_is_stored_and_bot_stays_silent(monkeypatch):
    wake = MagicMock()
    monkeypatch.setattr(monitor_classifier, "wake", wake)
    bot = _make_bot()
    await collect(_message(bot, "Sement qanchadan?", message_id=7))

    (message,) = await _messages()
    assert (message.chat_id, message.message_id, message.sender_id) == (GROUP_ID, 7, CUSTOMER_ID)
    assert (message.sender_username, message.sender_name, message.text) == ("mijoz1", "Vali Mijoz", "Sement qanchadan?")
    assert (message.is_staff, message.media_type, message.status) == (False, "text", "pending")
    assert message.sent_at_utc == T0 and str(message.local_date) == "2026-10-13"
    wake.assert_called_once()  # tasniflovchi uyg'otildi, lekin kutilmadi
    assert _nothing_written(bot)

    (group,) = await all_rows(Group)
    assert (group.chat_id, group.title, group.manager_username) == (GROUP_ID, "Mijoz guruhi", "@menejer_bek")


async def test_admin_messages_are_staff_and_are_not_queued_for_classification(monkeypatch):
    wake = MagicMock()
    monkeypatch.setattr(monitor_classifier, "wake", wake)
    bot = _make_bot()
    await collect(_message(bot, "65 mingdan", ADMIN, message_id=1))
    await collect(_message(bot, "Ertaga yetkazamiz", MANAGER, message_id=2))
    # Anonim admin guruh nomidan yozadi
    anonymous = _message(bot, "Qabul qilindi", _user(1087968824, "GroupAnonymousBot", is_bot=True), message_id=3,
                         sender_chat=SimpleNamespace(id=GROUP_ID), author_signature="Savdo bo'limi")
    await collect(anonymous)

    stored = await _messages()
    assert [(m.is_staff, m.status) for m in stored] == [(True, "staff")] * 3
    assert stored[2].sender_name == "Savdo bo'limi"
    wake.assert_not_called()
    bot.get_chat_administrators.assert_awaited_once()  # adminlar keshlanadi


async def test_media_type_caption_and_reply_are_stored():
    bot = _make_bot()
    question = _message(bot, "Narxi qancha?", message_id=1)
    await collect(question)
    await collect(_message(bot, None, message_id=2, caption="Mana shunaqa keldi", reply_to_message=SimpleNamespace(
        message_id=1, forum_topic_created=None), photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="big")]))
    await collect(_message(bot, None, message_id=3, voice=SimpleNamespace(file_id="v1")))
    await collect(_message(bot, None, message_id=4, sticker=SimpleNamespace(file_id="s1")))
    # Forum-guruhda topikni yaratgan servis xabari "reply" hisoblanmaydi
    await collect(_message(bot, "Salom", message_id=5, reply_to_message=SimpleNamespace(
        message_id=99, forum_topic_created=SimpleNamespace(name="Umumiy"))))

    stored = {m.message_id: m for m in await _messages()}
    assert (stored[2].media_type, stored[2].text, stored[2].file_id, stored[2].reply_to_message_id) == (
        "photo", "Mana shunaqa keldi", "big", 1)
    assert (stored[3].media_type, stored[3].text, stored[3].file_id) == ("voice", None, "v1")
    assert stored[4].media_type == "sticker"
    assert stored[5].reply_to_message_id is None


async def test_edit_updates_stored_message_and_unknown_old_message_is_not_added():
    bot = _make_bot()
    await collect(_message(bot, "Sement qanchadn?", message_id=7))
    edit_time = int((T0 + timedelta(minutes=1)).timestamp())
    await collect(_message(bot, "Sement qanchadan?", message_id=7, edit_date=edit_time), edited=True)
    # Bot ko'rmagan eski xabarning tahriri
    await collect(_message(bot, "Eski xabar", message_id=3, date=T0 - timedelta(days=3), edit_date=edit_time), edited=True)

    (message,) = await _messages()
    assert (message.text, message.edited_at) == ("Sement qanchadan?", T0 + timedelta(minutes=1))


@pytest.mark.parametrize("extra", [
    dict(content_type="new_chat_members"),
    dict(content_type="pinned_message"),
    dict(user=_user(999, "boshqa_bot", is_bot=True)),
    dict(sender_chat=SimpleNamespace(id=-1005555)),  # bog'langan kanal posti
    dict(chat_id=REPORT_CHAT),  # hisobot guruhi
    dict(chat_id=-1004402855904),  # kuzatuvdan chiqarilgan guruh
])
async def test_service_bot_channel_and_excluded_chats_are_not_stored(monkeypatch, extra):
    monkeypatch.setattr(group_monitor.settings, "GROUP_MONITOR_EXCLUDED_GROUP_IDS", "-1004402855904")
    bot = _make_bot()
    await collect(_message(bot, "matn", **extra))
    assert await _messages() == []


async def test_monitoring_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(group_monitor.settings, "MONITOR_ENABLED", False)
    bot = _make_bot()
    await collect(_message(bot, "Sement qanchadan?"))
    assert await _messages() == [] and await all_rows(Group) == []


async def test_handler_passes_message_on_and_survives_storage_errors(monkeypatch):
    monkeypatch.setattr(group_monitor, "collect", AsyncMock(side_effect=RuntimeError("baza band")))
    bot = _make_bot()
    with pytest.raises(SkipHandler):  # keyingi handlerlar (group_mentions) xabarni baribir oladi
        await group_monitor.handle_group_message(_message(bot, "Salom"))
    await group_monitor.handle_group_edit(_message(bot, "Salom"))


def _member_event(old_status, new_status, user=CUSTOMER, chat_id=GROUP_ID):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Mijoz guruhi", type="supergroup"),
        old_chat_member=SimpleNamespace(status=old_status, user=user, is_member=True),
        new_chat_member=SimpleNamespace(status=new_status, user=user, is_member=True),
    )


async def test_admin_change_refreshes_group_manager_on_next_message():
    bot = _make_bot()
    await collect(_message(bot, "Salom", message_id=1))
    bot.get_chat_administrators.return_value = [_admin(ADMIN, "Menejer")]

    await chat_events.on_group_member_changed(_member_event("member", "member"))  # oddiy a'zo -- hech narsa
    await collect(_message(bot, "Salom", message_id=2))
    assert (await monitor_db.get_group(GROUP_ID)).manager_username == "@menejer_bek"

    await chat_events.on_group_member_changed(_member_event("member", "administrator", ADMIN))
    await collect(_message(bot, "Salom", message_id=3))
    assert (await monitor_db.get_group(GROUP_ID)).manager_username == "@ali_staff"


async def test_new_group_is_registered_when_bot_is_added():
    event = _member_event("left", "administrator")
    await chat_events.on_bot_membership_changed(event)
    await chat_events.on_bot_membership_changed(_member_event("left", "member", chat_id=REPORT_CHAT))

    assert [(g.chat_id, g.title) for g in await all_rows(Group)] == [(GROUP_ID, "Mijoz guruhi")]
