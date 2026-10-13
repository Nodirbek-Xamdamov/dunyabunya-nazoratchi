"""`GROUP_ALERT_MODE=group`: mijoz savoli javobsiz qolsa bot guruhning O'ZIGA alohida xabar
yozib, shu guruhga biriktirilgan barcha menejerlarni belgilaydi (`app/group_timers.py`).
Bot mijoz guruhiga yozadigan yagona xabar shu -- qolgan hamma holatda u jim turadi."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select

from app import ai_processor, group_registry, group_stats, group_timers
from app.bot.handlers import group_monitor
from app.bot.handlers.group_monitor import get_alert_managers, get_unanswered_thread_id, process_group_message
from app.database import async_session_maker
from app.group_ai import MessageAnalysis
from app.group_registry import ManagerInfo
from app.models import DailyStats, HubTopic, InquiryTimer, StaffReplyWait

HUB_ID = -1001111111111
GROUP_ID = -1002222222222
STAFF_ID = 100
MANAGER_ID = 101
CUSTOMER_ID = 200
T0 = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)

QUESTION = MessageAnalysis(needs_reply=True, about_company=True, topic="price")
CHATTER = MessageAnalysis()


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    settings = group_monitor.settings
    monkeypatch.setattr(settings, "FEEDBACK_HUB_GROUP_ID", HUB_ID)
    monkeypatch.setattr(settings, "GROUP_ALERT_MODE", "group")
    monkeypatch.setattr(settings, "STAFF_REPLY_TIMEOUT_MINUTES", 5)
    monkeypatch.setattr(settings, "GROUP_ALERT_REPEAT_MINUTES", 60)
    monkeypatch.setattr(settings, "GROUP_ALERT_TEXT", "Mijozning savoli javobsiz qoldi. Iltimos, javob bering.")
    monkeypatch.setattr(settings, "GROUP_MONITOR_EXCLUDED_GROUP_IDS", "")
    monkeypatch.setattr(settings, "HUB_AUTO_CREATE_TOPICS", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    group_monitor.reset_state()


def _user(user_id, username=None, full_name="Ism"):
    return SimpleNamespace(id=user_id, username=username, full_name=full_name, is_bot=False)


STAFF = _user(STAFF_ID, "ali_staff", "Ali Xodim")
MANAGER = _user(MANAGER_ID, "menejer_bek", "Bek Menejer")
CUSTOMER = _user(CUSTOMER_ID, "mijoz1", "Vali Mijoz")


def _make_bot(admins=()):
    bot = MagicMock()

    async def get_chat_member(chat_id, user_id):
        return SimpleNamespace(status="member" if user_id in (STAFF_ID, MANAGER_ID) else "left")

    bot.get_chat_member = AsyncMock(side_effect=get_chat_member)
    bot.get_chat_administrators = AsyncMock(return_value=list(admins))
    bot.send_message = AsyncMock()
    bot.copy_message = AsyncMock()
    return bot


def _admin(user, title):
    return SimpleNamespace(user=user, custom_title=title, status="administrator")


def _message(bot, text, user, *, message_id=1, date=T0, reply_to=None):
    return SimpleNamespace(
        bot=bot, text=text, caption=None, entities=None, caption_entities=None, content_type="text",
        chat=SimpleNamespace(id=GROUP_ID, title="Mijoz guruhi", username=None, type="supergroup"),
        from_user=user, message_id=message_id, date=date, reply_to_message=reply_to,
        message_thread_id=None, forum_topic_created=None, forum_topic_edited=None,
    )


async def _say(monkeypatch, bot, text, user, analysis, **kwargs):
    monkeypatch.setattr(ai_processor, "analyze", AsyncMock(return_value=analysis))
    await process_group_message(_message(bot, text, user, **kwargs))


async def _timers():
    async with async_session_maker() as session:
        return (await session.execute(select(InquiryTimer))).scalars().all()


async def _check(bot, minutes):
    return await group_timers.send_due_alerts(
        bot, get_alert_managers, get_unanswered_thread_id, now=T0 + timedelta(minutes=minutes)
    )


async def test_unanswered_question_tags_all_group_managers_in_the_group_itself(monkeypatch):
    bot = _make_bot([_admin(MANAGER, "Menejer")])
    await group_registry.add_manager(GROUP_ID, "@Qolda_Menejer")
    await _say(monkeypatch, bot, "Sement qanchadan?", CUSTOMER, QUESTION, message_id=7)

    (timer,) = await _timers()
    assert timer.wait_minutes == 5 and timer.message_id == 7 and timer.message_text == "Sement qanchadan?"
    assert timer.due_at.replace(tzinfo=timezone.utc) == T0 + timedelta(minutes=5)
    bot.send_message.assert_not_called()  # muddat o'tmaguncha bot jim

    assert await _check(bot, 4.9) == 0
    assert await _check(bot, 5) == 1
    assert await _check(bot, 6) == 0  # bir marta

    (call,) = bot.send_message.await_args_list
    assert call.args[0] == GROUP_ID
    assert call.args[1] == "@qolda_menejer @menejer_bek\nMijozning savoli javobsiz qoldi. Iltimos, javob bering."
    # Alohida xabar: hech qaysi xabarga reply yoki iqtibos emas
    assert not {"reply_to_message_id", "reply_parameters"} & set(call.kwargs)
    async with async_session_maker() as session:
        counts = {row.category_name: row.count for row in (await session.execute(select(DailyStats))).scalars()}
    assert counts[group_stats.STAT_ALERTS] == 1


async def test_any_staff_message_cancels_the_timer(monkeypatch):
    bot = _make_bot([_admin(MANAGER, "Menejer")])
    await _say(monkeypatch, bot, "Sement qanchadan?", CUSTOMER, QUESTION, message_id=7)
    # Menejer emas, oddiy xodim yozdi -- baribir javob hisoblanadi
    await _say(monkeypatch, bot, "65 mingdan", STAFF, CHATTER, message_id=8, date=T0 + timedelta(minutes=2))

    assert await _timers() == []
    assert await _check(bot, 30) == 0
    bot.send_message.assert_not_called()


async def test_staff_extension_resets_the_timer(monkeypatch):
    bot = _make_bot([_admin(MANAGER, "Menejer")])
    await _say(monkeypatch, bot, "Sement qanchadan?", CUSTOMER, QUESTION)
    await _say(
        monkeypatch, bot, "10 minutda javob beramiz", STAFF, MessageAnalysis(promised_minutes=10),
        message_id=2, date=T0 + timedelta(minutes=1),
    )

    (timer,) = await _timers()
    assert timer.promised is True and timer.wait_minutes == 10
    assert await _check(bot, 6) == 0  # eski 5 daqiqalik muddat endi amal qilmaydi
    assert await _check(bot, 11) == 1
    assert bot.send_message.await_args.args[0] == GROUP_ID


async def test_staff_answer_after_extension_cancels_the_timer(monkeypatch):
    bot = _make_bot([_admin(MANAGER, "Menejer")])
    await _say(monkeypatch, bot, "Sement qanchadan?", CUSTOMER, QUESTION)
    await _say(monkeypatch, bot, "10 minutda javob beramiz", STAFF, MessageAnalysis(promised_minutes=10), message_id=2)
    await _say(monkeypatch, bot, "65 mingdan", STAFF, CHATTER, message_id=3, date=T0 + timedelta(minutes=7))

    assert await _timers() == []
    assert await _check(bot, 60) == 0


async def test_timer_counts_from_the_first_unanswered_question(monkeypatch):
    bot = _make_bot([_admin(MANAGER, "Menejer")])
    await _say(monkeypatch, bot, "Sement qanchadan?", CUSTOMER, QUESTION, message_id=1)
    await _say(monkeypatch, bot, "Javob bormi?", CUSTOMER, QUESTION, message_id=2, date=T0 + timedelta(minutes=4))

    (timer,) = await _timers()
    assert timer.message_id == 1
    assert await _check(bot, 5) == 1


async def test_ordinary_messages_start_no_timer_even_when_addressed_to_staff(monkeypatch):
    """Guruhga yoziladigan ogohlantirish "rahmat" kabi xabar uchun ketmasligi kerak: kutish faqat
    model xabarni javob kutadigan savol deb topganda ochiladi."""
    bot = _make_bot([_admin(MANAGER, "Menejer")])
    staff_message = _message(bot, "65 mingdan", STAFF, message_id=1)
    await _say(monkeypatch, bot, "Ok rahmat", CUSTOMER, MessageAnalysis(about_company=True), message_id=2, reply_to=staff_message)
    await _say(monkeypatch, bot, "Kecha futbolni ko'rdingmi?", CUSTOMER, CHATTER, message_id=3)

    assert await _timers() == []
    # "dm" rejimining shaxsiy eslatmalari bu rejimda ochilmaydi
    async with async_session_maker() as session:
        assert (await session.execute(select(StaffReplyWait))).scalars().all() == []


async def test_question_already_answered_while_being_analyzed_starts_no_timer(monkeypatch):
    bot = _make_bot([_admin(MANAGER, "Menejer")])
    await _say(monkeypatch, bot, "65 mingdan", STAFF, CHATTER, message_id=2, date=T0 + timedelta(seconds=20))
    await _say(monkeypatch, bot, "Sement qanchadan?", CUSTOMER, QUESTION, message_id=1, date=T0)
    assert await _timers() == []


async def test_group_without_managers_is_reported_to_the_hub_not_to_the_group(monkeypatch):
    async with async_session_maker() as session:
        session.add(HubTopic(thread_id=40, name="Javobsiz savollar"))
        await session.commit()
    bot = _make_bot()  # guruhda "Menejer" unvonli admin yo'q, qo'lda ham biriktirilmagan
    await _say(monkeypatch, bot, "Sement qanchadan?", CUSTOMER, QUESTION, message_id=7)

    assert await _check(bot, 5) == 1
    (call,) = bot.send_message.await_args_list
    assert call.args[0] == HUB_ID and call.kwargs["message_thread_id"] == 40
    text = call.args[1]
    assert "Mijoz guruhi" in text and "Vali Mijoz" in text and "5 daqiqa" in text
    assert "menejer biriktirilmagan" in text and "Sement qanchadan?" in text
    assert "t.me/c/2222222222/7" in text


async def test_alert_falls_back_to_the_hub_when_bot_cannot_write_in_the_group(monkeypatch):
    bot = _make_bot([_admin(MANAGER, "Menejer")])
    await _say(monkeypatch, bot, "Sement qanchadan?", CUSTOMER, QUESTION)

    async def send_message(chat_id, *args, **kwargs):
        if chat_id == GROUP_ID:
            raise TelegramForbiddenError(method=MagicMock(), message="not enough rights to send text messages")

    bot.send_message = AsyncMock(side_effect=send_message)
    assert await _check(bot, 5) == 1
    assert [call.args[0] for call in bot.send_message.await_args_list] == [GROUP_ID, HUB_ID]
    assert "yoza olmadi" in bot.send_message.await_args.args[1]


async def test_managers_are_not_tagged_again_until_repeat_window_passes(monkeypatch):
    bot = _make_bot([_admin(MANAGER, "Menejer")])
    await _say(monkeypatch, bot, "Sement qanchadan?", CUSTOMER, QUESTION, message_id=1)
    assert await _check(bot, 5) == 1

    # Hali hech kim javob yozmadi; mijoz yana so'radi -- menejerlar qayta belgilanmaydi
    await _say(monkeypatch, bot, "Allo?", CUSTOMER, QUESTION, message_id=2, date=T0 + timedelta(minutes=20))
    assert await _check(bot, 40) == 0

    # Bir soatdan keyin yangi savol -- kutish qaytadan boshlanadi
    later = T0 + timedelta(minutes=70)
    await _say(monkeypatch, bot, "Hech kim yo'qmi?", CUSTOMER, QUESTION, message_id=3, date=later)
    (timer,) = await _timers()
    assert timer.message_id == 3 and timer.notified_at is None
    assert await _check(bot, 76) == 1
    assert len(bot.send_message.await_args_list) == 2


async def test_staff_reply_after_alert_allows_a_fresh_timer_immediately(monkeypatch):
    bot = _make_bot([_admin(MANAGER, "Menejer")])
    await _say(monkeypatch, bot, "Sement qanchadan?", CUSTOMER, QUESTION, message_id=1)
    await _check(bot, 5)
    await _say(monkeypatch, bot, "65 mingdan", MANAGER, CHATTER, message_id=2, date=T0 + timedelta(minutes=6))
    await _say(monkeypatch, bot, "Yetkazib berasizmi?", CUSTOMER, QUESTION, message_id=3, date=T0 + timedelta(minutes=8))

    (timer,) = await _timers()
    assert timer.message_id == 3
    assert await _check(bot, 13) == 1


def test_mentions_use_usernames_and_fall_back_to_a_name_link():
    managers = [
        ManagerInfo(1, "ali", "Ali", "title"),
        ManagerInfo(2, None, "Bek <Menejer>", "title"),
        ManagerInfo(None, None, "Ismi ham yo'q", "manual"),  # belgilab bo'lmaydi
        ManagerInfo(None, "ali", None, "manual"),  # takror
    ]
    assert group_timers.build_mentions(managers) == '@ali <a href="tg://user?id=2">Bek &lt;Menejer&gt;</a>'
    assert group_timers.build_mentions([]) == ""
