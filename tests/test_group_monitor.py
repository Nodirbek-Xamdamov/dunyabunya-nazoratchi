"""app.bot.handlers.group_monitor -- mijoz guruhlarini kuzatish: xabarni tasniflab 8 ta toifadan
biriga ajratish, shikoyat/ijobiy fikr/"tovar yo'q" xabarlarini ichki guruh topiklariga bir marta
tashlash, kompaniyaga oid xabarlarni bazaga yozish, xodim javobini kutish va eslatmalar.

Bu fayldagi testlar `GROUP_ALERT_MODE=dm` rejimida: bot mijoz guruhiga HECH NARSA yozmaydi.
Guruhga yoziladigan ogohlantirish (`group` rejimi) -- `test_group_timers.py`."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select

from app import ai_processor, group_registry, group_stats
from app.ai_processor import MediaContent
from app.bot.handlers import group_monitor
from app.bot.handlers.group_monitor import process_group_message, send_due_reminders
from app.database import async_session_maker
from app.group_ai import MessageAnalysis
from app.models import DailyStats, Group, HubTopic, Manager, MessageLog, StaffMember, StaffReplyWait

HUB_ID = -1001111111111
GROUP_ID = -1002222222222
STAFF_ID = 100
OTHER_STAFF_ID = 101
CUSTOMER_ID = 200
T0 = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _monitor_settings(monkeypatch):
    monkeypatch.setattr(group_monitor.settings, "FEEDBACK_HUB_GROUP_ID", HUB_ID)
    monkeypatch.setattr(group_monitor.settings, "STAFF_REPLY_TIMEOUT_MINUTES", 5)
    monkeypatch.setattr(group_monitor.settings, "GROUP_MONITOR_EXCLUDED_GROUP_IDS", "")
    monkeypatch.setattr(group_monitor.settings, "OPENAI_API_KEY", None)
    monkeypatch.setattr(group_monitor.settings, "GROUP_ALERT_MODE", "dm")
    monkeypatch.setattr(group_monitor.settings, "HUB_AUTO_CREATE_TOPICS", True)
    group_monitor.reset_state()


def _make_bot():
    bot = MagicMock()

    async def get_chat_member(chat_id, user_id):
        return SimpleNamespace(status="member" if user_id in (STAFF_ID, OTHER_STAFF_ID) else "left")

    bot.get_chat_member = AsyncMock(side_effect=get_chat_member)
    bot.get_chat_administrators = AsyncMock(return_value=[])
    bot.send_message = AsyncMock()
    bot.copy_message = AsyncMock()
    bot.download = AsyncMock()
    # Standart holat: botda ichki guruhda topik yaratish huquqi yo'q
    bot.create_forum_topic = AsyncMock(
        side_effect=TelegramBadRequest(method=MagicMock(), message="not enough rights to create a topic")
    )
    return bot


def _user(user_id, username=None, full_name="Ism"):
    return SimpleNamespace(id=user_id, username=username, full_name=full_name, is_bot=False)


def _make_message(
    bot, text, user, *, chat_id=GROUP_ID, message_id=1, date=T0, reply_to=None, entities=None,
    thread_id=None, topic_created=None, voice=None, video=None, caption=None, photo=None, media_group_id=None,
    title="Mijoz guruhi",
):
    return SimpleNamespace(
        bot=bot,
        text=text,
        caption=caption,
        voice=voice,
        video=video,
        photo=photo,
        media_group_id=media_group_id,
        entities=entities,
        caption_entities=None,
        content_type="text",
        chat=SimpleNamespace(id=chat_id, title=title, username=None, type="supergroup"),
        from_user=user,
        message_id=message_id,
        date=date,
        reply_to_message=reply_to,
        message_thread_id=thread_id,
        forum_topic_created=topic_created,
        forum_topic_edited=None,
    )


STAFF = _user(STAFF_ID, "ali_staff", "Ali Xodim")
CUSTOMER = _user(CUSTOMER_ID, "mijoz1", "Vali Mijoz")


def _fake_analysis(monkeypatch, analysis):
    """Tasniflovchini soxtalashtiradi (media tayyorlash haqiqiy qoladi)."""
    analyze = AsyncMock(return_value=analysis)
    monkeypatch.setattr(ai_processor, "analyze", analyze)
    return analyze


async def _all(model):
    async with async_session_maker() as session:
        return (await session.execute(select(model))).scalars().all()


async def _waits():
    return await _all(StaffReplyWait)


async def _stats():
    return {row.category_name: row.count for row in await _all(DailyStats)}


def _sent_chat_ids(bot):
    return [call.args[0] for call in bot.send_message.await_args_list] + [
        call.args[0] for call in bot.copy_message.await_args_list
    ]


async def _customer_asks_staff(bot, text="Ali aka, narxi qancha?", date=T0, message_id=10):
    staff_message = _make_message(bot, "Assalomu alaykum", STAFF, message_id=5)
    await process_group_message(
        _make_message(bot, text, CUSTOMER, message_id=message_id, date=date, reply_to=staff_message)
    )


# ---------------------------------------------------------------------------
# Xodim javobini kutish ("dm" rejimi)
# ---------------------------------------------------------------------------


async def test_customer_reply_to_staff_starts_five_minute_wait():
    bot = _make_bot()
    await _customer_asks_staff(bot)

    (wait,) = await _waits()
    assert wait.staff_chat_id == STAFF_ID
    assert wait.wait_minutes == 5
    assert wait.due_at.replace(tzinfo=timezone.utc) == T0 + timedelta(minutes=5)
    assert wait.message_link == "https://t.me/c/2222222222/10"
    assert _sent_chat_ids(bot) == []


async def test_customer_mention_of_staff_username_starts_wait():
    bot = _make_bot()
    # Xodim avval guruhda yozgan -- shu orqali username -> chat_id ma'lum bo'ladi
    await process_group_message(_make_message(bot, "Salom", STAFF, message_id=1))
    entity = SimpleNamespace(type="mention", user=None, extract_from=lambda text: "@Ali_Staff")
    await process_group_message(
        _make_message(bot, "@Ali_Staff qachon keladi?", CUSTOMER, message_id=2, entities=[entity])
    )

    (wait,) = await _waits()
    assert wait.staff_chat_id == STAFF_ID


async def test_message_not_addressed_to_staff_starts_no_wait():
    bot = _make_bot()
    other_customer_message = _make_message(bot, "salom", _user(201), message_id=5)
    await process_group_message(_make_message(bot, "Narxi qancha?", CUSTOMER, reply_to=other_customer_message))
    assert await _waits() == []


async def test_forum_topic_service_reply_is_not_an_address():
    bot = _make_bot()
    topic_root = _make_message(bot, None, STAFF, message_id=3, topic_created=SimpleNamespace(name="Umumiy"))
    await process_group_message(_make_message(bot, "Narxi qancha?", CUSTOMER, reply_to=topic_root))
    assert await _waits() == []


async def test_staff_reply_in_time_forgets_wait():
    bot = _make_bot()
    await _customer_asks_staff(bot)
    await process_group_message(
        _make_message(bot, "Narxi 50 000 so'm", STAFF, message_id=11, date=T0 + timedelta(minutes=2))
    )

    assert await _waits() == []
    assert await send_due_reminders(bot, now=T0 + timedelta(minutes=30)) == 0
    assert _sent_chat_ids(bot) == []


async def test_no_reply_sends_reminder_to_staff_private_chat_once():
    bot = _make_bot()
    await _customer_asks_staff(bot)

    assert await send_due_reminders(bot, now=T0 + timedelta(minutes=4, seconds=59)) == 0
    assert await send_due_reminders(bot, now=T0 + timedelta(minutes=5)) == 1
    assert await send_due_reminders(bot, now=T0 + timedelta(minutes=6)) == 0

    (call,) = bot.send_message.await_args_list
    assert call.args[0] == STAFF_ID
    assert "5 daqiqa bo'ldi" in call.args[1]
    assert "Vali Mijoz" in call.args[1]
    assert (await _stats())[group_stats.STAT_ALERTS] == 1


async def test_staff_promise_extends_wait_to_ten_minutes():
    bot = _make_bot()
    await _customer_asks_staff(bot)
    promise_at = T0 + timedelta(minutes=1)
    await process_group_message(
        _make_message(bot, "10 minutda javob beraman", STAFF, message_id=11, date=promise_at)
    )

    (wait,) = await _waits()
    assert wait.promised is True and wait.wait_minutes == 10
    assert await send_due_reminders(bot, now=T0 + timedelta(minutes=6)) == 0
    assert await send_due_reminders(bot, now=promise_at + timedelta(minutes=10)) == 1
    (call,) = bot.send_message.await_args_list
    assert call.args[0] == STAFF_ID
    assert "10 daqiqa bo'ldi" in call.args[1]


async def test_staff_reply_after_promise_forgets_wait():
    bot = _make_bot()
    await _customer_asks_staff(bot)
    await process_group_message(
        _make_message(bot, "10 minutda javob beraman", STAFF, message_id=11, date=T0 + timedelta(minutes=1))
    )
    await process_group_message(
        _make_message(bot, "Narxi 50 000 so'm", STAFF, message_id=12, date=T0 + timedelta(minutes=8))
    )

    assert await _waits() == []
    assert await send_due_reminders(bot, now=T0 + timedelta(minutes=30)) == 0


async def test_other_staff_replying_to_customer_message_closes_wait():
    bot = _make_bot()
    await _customer_asks_staff(bot, message_id=10)
    customer_message = _make_message(bot, "Ali aka, narxi qancha?", CUSTOMER, message_id=10)
    await process_group_message(
        _make_message(
            bot, "Narxi 50 000 so'm", _user(OTHER_STAFF_ID, "boshqa"), message_id=11, reply_to=customer_message
        )
    )
    assert await _waits() == []


async def test_reminder_falls_back_to_hub_when_staff_has_not_started_bot():
    bot = _make_bot()
    await _customer_asks_staff(bot)

    async def send_message(chat_id, *args, **kwargs):
        if chat_id == STAFF_ID:
            raise TelegramForbiddenError(method=MagicMock(), message="bot can't initiate conversation with a user")

    bot.send_message = AsyncMock(side_effect=send_message)
    assert await send_due_reminders(bot, now=T0 + timedelta(minutes=5)) == 1
    assert [call.args[0] for call in bot.send_message.await_args_list] == [STAFF_ID, HUB_ID]
    assert "@ali_staff" in bot.send_message.await_args_list[1].args[1]


async def test_new_customer_message_after_reminder_restarts_wait():
    bot = _make_bot()
    await _customer_asks_staff(bot)
    await send_due_reminders(bot, now=T0 + timedelta(minutes=5))
    later = T0 + timedelta(minutes=20)
    await _customer_asks_staff(bot, text="Javob bormi?", date=later, message_id=20)

    (wait,) = await _waits()
    assert wait.notified_at is None and wait.message_id == 20
    assert await send_due_reminders(bot, now=later + timedelta(minutes=5)) == 1


# ---------------------------------------------------------------------------
# Ichki guruh topiklariga tashlash
# ---------------------------------------------------------------------------


async def _add_topics(*extra):
    async with async_session_maker() as session:
        session.add_all(
            [
                HubTopic(thread_id=11, name="Shikoyatlar"),
                HubTopic(thread_id=12, name="Ijobiy sharhlar"),
                HubTopic(thread_id=13, name="Tovarlar"),
                *extra,
            ]
        )
        await session.commit()


@pytest.mark.parametrize(
    "analysis, thread_id, title",
    [
        # Toifaning o'z topigi yo'q va uni yaratib bo'lmadi -- shikoyat umumiy "Shikoyatlar"ga tushadi
        (MessageAnalysis(category="complaint", about_company=True, topic="delivery"), 11, "Dastavka va Logistika"),
        (MessageAnalysis(category="praise", about_company=True), 12, "Ijobiy Fikrlar"),
        (MessageAnalysis(out_of_stock=True, about_company=True), 13, "Tovar yo'q"),
    ],
)
async def test_customer_message_is_sent_once_to_matching_hub_topic(monkeypatch, analysis, thread_id, title):
    await _add_topics()
    _fake_analysis(monkeypatch, analysis)
    bot = _make_bot()
    await process_group_message(_make_message(bot, "matn <b>", CUSTOMER, message_id=7))

    # Bitta toza xabar: sarlavha + matn iqtibosi; manba guruhga hech narsa yozilmaydi
    assert _sent_chat_ids(bot) == [HUB_ID]
    call = bot.send_message.await_args
    assert call.kwargs["message_thread_id"] == thread_id
    text = call.args[1]
    assert title in text
    assert "Mijoz guruhi" in text and "Vali Mijoz (@mijoz1)" in text
    assert 'href="https://t.me/c/2222222222/7"' in text
    assert "<blockquote>matn &lt;b&gt;</blockquote>" in text


async def test_complaint_goes_to_its_own_category_topic_when_it_exists(monkeypatch):
    await _add_topics(HubTopic(thread_id=21, name="dastavka va logistika"), HubTopic(thread_id=22, name="Sifat"))
    bot = _make_bot()

    _fake_analysis(monkeypatch, MessageAnalysis(category="complaint", about_company=True, topic="delivery"))
    await process_group_message(_make_message(bot, "Uch kundan beri kutyapman", CUSTOMER, message_id=1))
    assert bot.send_message.await_args.kwargs["message_thread_id"] == 21
    assert "🔴 Shikoyat" in bot.send_message.await_args.args[1]

    # Qo'lda qisqa nomlangan topik ham nomidagi bo'lak bo'yicha taniladi
    _fake_analysis(monkeypatch, MessageAnalysis(category="complaint", about_company=True, topic="quality"))
    await process_group_message(_make_message(bot, "Yarmi singan", CUSTOMER, message_id=2))
    assert bot.send_message.await_args.kwargs["message_thread_id"] == 22
    bot.create_forum_topic.assert_not_called()


async def test_missing_category_topic_is_created_once_and_reused(monkeypatch):
    await _add_topics()
    bot = _make_bot()
    bot.create_forum_topic = AsyncMock(return_value=SimpleNamespace(message_thread_id=77))
    _fake_analysis(monkeypatch, MessageAnalysis(category="complaint", about_company=True, topic="price"))

    await process_group_message(_make_message(bot, "Qimmat oldingiz", CUSTOMER, message_id=1))
    await process_group_message(_make_message(bot, "Yana qimmat", CUSTOMER, message_id=2))

    bot.create_forum_topic.assert_awaited_once_with(HUB_ID, "Narx va To'lovlar")
    assert [call.kwargs["message_thread_id"] for call in bot.send_message.await_args_list] == [77, 77]
    async with async_session_maker() as session:
        assert (await session.get(HubTopic, 77)).name == "Narx va To'lovlar"


async def test_topic_creation_is_not_retried_after_failure_and_can_be_disabled(monkeypatch):
    bot = _make_bot()
    _fake_analysis(monkeypatch, MessageAnalysis(category="complaint", about_company=True, topic="price"))
    await process_group_message(_make_message(bot, "Qimmat", CUSTOMER, message_id=1))
    await process_group_message(_make_message(bot, "Qimmat", CUSTOMER, message_id=2))

    bot.create_forum_topic.assert_awaited_once()  # huquq yo'q -- har xabarda qayta urinilmaydi
    # Topik yo'q -- xabar yo'qolmaydi, ichki guruhning umumiy qismiga tushadi
    assert [call.kwargs["message_thread_id"] for call in bot.send_message.await_args_list] == [None, None]

    group_monitor.reset_state()
    monkeypatch.setattr(group_monitor.settings, "HUB_AUTO_CREATE_TOPICS", False)
    bot.create_forum_topic.reset_mock()
    await process_group_message(_make_message(bot, "Qimmat", CUSTOMER, message_id=3))
    bot.create_forum_topic.assert_not_called()


async def test_topic_ids_can_be_set_in_env(monkeypatch):
    monkeypatch.setattr(group_monitor.settings, "HUB_TOPIC_IDS", "delivery=31, stock=32")
    monkeypatch.setattr(group_monitor.settings, "HUB_TOPIC_PRAISE_ID", 33)
    assert await group_monitor.get_topic_thread_id("delivery") == 31
    assert await group_monitor.get_topic_thread_id("stock") == 32
    assert await group_monitor.get_topic_thread_id("positive") == 33
    assert await group_monitor.get_topic_thread_id("quality") is None


async def test_quality_topic_is_not_mistaken_for_stock_topic():
    """"Mahsulot Sifati va Nuqsonlar" nomidagi "mahsulot" so'zi uni "tovar yo'q" topigiga aylantirmaydi."""
    async with async_session_maker() as session:
        session.add(HubTopic(thread_id=5, name="Mahsulot Sifati va Nuqsonlar"))
        await session.commit()
    assert await group_monitor.get_topic_thread_id("quality") == 5
    assert await group_monitor.get_topic_thread_id("stock") is None


async def test_same_message_is_never_sent_to_a_topic_twice(monkeypatch):
    await _add_topics()
    _fake_analysis(monkeypatch, MessageAnalysis(category="complaint", about_company=True, out_of_stock=True))
    bot = _make_bot()
    message = _make_message(bot, "Tovar yo'q deb aldadingiz", CUSTOMER, message_id=7)
    await process_group_message(message)
    await process_group_message(message)  # Telegram xabarni qayta yetkazdi

    # Ikki xil topikka (shikoyat va "tovar yo'q") bittadan
    assert [call.kwargs["message_thread_id"] for call in bot.send_message.await_args_list] == [11, 13]


async def test_staff_out_of_stock_message_goes_to_stock_topic_and_ordinary_message_nowhere():
    await _add_topics()
    bot = _make_bot()
    await process_group_message(_make_message(bot, "Ertaga yetkazamiz", STAFF, message_id=1))
    assert _sent_chat_ids(bot) == []

    await process_group_message(_make_message(bot, "Bu tovar yo'q ekan", STAFF, message_id=2))
    assert bot.send_message.await_args.kwargs["message_thread_id"] == 13
    assert set(_sent_chat_ids(bot)) == {HUB_ID}


async def test_hub_messages_teach_topics_and_staff_but_are_not_monitored():
    bot = _make_bot()
    topic_root = SimpleNamespace(forum_topic_created=SimpleNamespace(name="Shikoyatlar"))
    await process_group_message(
        _make_message(bot, "tovar yo'q ekan", STAFF, chat_id=HUB_ID, thread_id=42, reply_to=topic_root)
    )

    assert await group_monitor.get_topic_thread_id("complaint") == 42
    async with async_session_maker() as session:
        assert (await session.get(StaffMember, STAFF_ID)).normalized_username == "ali_staff"
    assert _sent_chat_ids(bot) == []
    assert await _all(Group) == [] and await _all(MessageLog) == []


async def test_customers_chatting_with_each_other_are_not_forwarded():
    """Kalit-so'z rejimi: mijozlar o'zaro gaplashganda "yomon"/"rahmat" kabi so'zlar
    uchrasa ham hech narsa tashlanmaydi -- faqat xodimga murojaat qilingan xabar tekshiriladi."""
    await _add_topics()
    bot = _make_bot()
    first = _make_message(bot, "Bugun havo juda yomon, norozi bo'ldim", _user(201, full_name="Sardor"), message_id=1)
    await process_group_message(first)
    await process_group_message(_make_message(bot, "Ha, katta rahmat sizga", CUSTOMER, message_id=2, reply_to=first))
    assert _sent_chat_ids(bot) == []

    staff_message = _make_message(bot, "Assalomu alaykum", STAFF, message_id=3)
    await process_group_message(
        _make_message(bot, "Xizmatingiz juda yomon", CUSTOMER, message_id=4, reply_to=staff_message)
    )
    assert bot.send_message.await_args.kwargs["message_thread_id"] == 11


async def test_classifier_receives_previous_messages_as_context(monkeypatch):
    analyze = _fake_analysis(monkeypatch, MessageAnalysis())
    bot = _make_bot()
    first = _make_message(bot, "Kecha futbolni ko'rdingmi?", _user(201, full_name="Sardor"), message_id=1)
    await process_group_message(first)
    await process_group_message(_make_message(bot, "Ha, yomon o'ynashdi", CUSTOMER, message_id=2, reply_to=first))

    assert analyze.await_args_list[0].kwargs["context"] == ""
    assert analyze.await_args_list[1].args == ("Ha, yomon o'ynashdi", False)
    assert analyze.await_args_list[1].kwargs == {
        "context": "[mijoz] Sardor: Kecha futbolni ko'rdingmi?",
        "addressed_to_staff": False,
        "media": MediaContent(),
    }


# ---------------------------------------------------------------------------
# Bazaga yozish va sanoqlar
# ---------------------------------------------------------------------------


async def test_company_related_message_is_logged_with_category_and_sentiment(monkeypatch):
    await _add_topics()
    _fake_analysis(monkeypatch, MessageAnalysis(category="complaint", about_company=True, topic="quality"))
    bot = _make_bot()
    await process_group_message(_make_message(bot, "Sementingiz qotib qolgan", CUSTOMER, message_id=7))

    (log,) = await _all(MessageLog)
    assert (log.group_id, log.user_id, log.username, log.message_id) == (GROUP_ID, CUSTOMER_ID, "mijoz1", 7)
    assert log.message_text == "Sementingiz qotib qolgan"
    assert (log.category, log.sentiment) == ("Mahsulot Sifati va Nuqsonlar", "negative")
    assert log.is_company_related is True and log.forwarded is True and log.media_type == "text"
    assert log.timestamp.replace(tzinfo=timezone.utc) == T0
    assert log.message_link == "https://t.me/c/2222222222/7"


async def test_neutral_company_question_is_logged_but_not_forwarded(monkeypatch):
    await _add_topics()
    _fake_analysis(monkeypatch, MessageAnalysis(about_company=True, topic="price", needs_reply=True))
    bot = _make_bot()
    await process_group_message(_make_message(bot, "Sement qanchadan?", CUSTOMER))

    (log,) = await _all(MessageLog)
    assert (log.category, log.sentiment, log.forwarded) == ("Narx va To'lovlar", "neutral", False)
    assert _sent_chat_ids(bot) == []


async def test_chatter_is_not_logged_or_forwarded_only_counted(monkeypatch):
    await _add_topics()
    _fake_analysis(monkeypatch, MessageAnalysis())  # kompaniyaga aloqasiz
    bot = _make_bot()
    await process_group_message(_make_message(bot, "Kecha futbolni ko'rdingmi?", CUSTOMER, message_id=1))
    await process_group_message(_make_message(bot, "Yo'q, kim yutdi?", _user(201), message_id=2))
    await process_group_message(_make_message(bot, "Assalomu alaykum", STAFF, message_id=3))

    assert await _all(MessageLog) == []
    assert _sent_chat_ids(bot) == []
    assert await _stats() == {group_stats.STAT_FILTERED: 2, group_stats.STAT_STAFF: 1}


async def test_group_is_registered_on_first_message_and_inactive_group_is_ignored(monkeypatch):
    analyze = _fake_analysis(monkeypatch, MessageAnalysis(category="complaint", about_company=True))
    bot = _make_bot()
    await process_group_message(_make_message(bot, "Yomon xizmat", CUSTOMER, message_id=1))

    (group,) = await _all(Group)
    assert (group.group_id, group.title, group.is_active) == (GROUP_ID, "Mijoz guruhi", True)

    await group_registry.set_group_active(GROUP_ID, False)
    analyze.reset_mock()
    bot.send_message.reset_mock()
    await process_group_message(_make_message(bot, "Yana yomon xizmat", CUSTOMER, message_id=2))

    analyze.assert_not_called()
    assert _sent_chat_ids(bot) == [] and len(await _all(MessageLog)) == 1


async def test_group_title_change_is_saved():
    bot = _make_bot()
    await process_group_message(_make_message(bot, "salom", CUSTOMER, message_id=1))
    await process_group_message(_make_message(bot, "salom", CUSTOMER, message_id=2, title="Yangi nom"))
    (group,) = await _all(Group)
    assert group.title == "Yangi nom"


# ---------------------------------------------------------------------------
# Guruh menejerlari
# ---------------------------------------------------------------------------


def _admin(user, title):
    return SimpleNamespace(user=user, custom_title=title, status="administrator")


MANAGER = _user(OTHER_STAFF_ID, "menejer_bek", "Bek Menejer")


async def _customer_asks_nobody(monkeypatch, bot, needs_reply=True):
    _fake_analysis(monkeypatch, MessageAnalysis(needs_reply=needs_reply, about_company=True))
    await process_group_message(_make_message(bot, "Menga sement kerak, qanchadan?", CUSTOMER, message_id=30))


async def test_unaddressed_question_waits_for_group_manager_by_admin_title(monkeypatch):
    bot = _make_bot()
    bot.get_chat_administrators.return_value = [
        _admin(STAFF, None),  # xodim, lekin unvoni yo'q
        _admin(MANAGER, "Menejer"),
        _admin(_user(300, "begona"), "Menejer"),  # unvoni bor, lekin ichki guruhda yo'q
    ]
    await _customer_asks_nobody(monkeypatch, bot)

    (wait,) = await _waits()
    assert wait.staff_chat_id == OTHER_STAFF_ID and wait.addressed is False

    assert await send_due_reminders(bot, now=T0 + timedelta(minutes=5)) == 1
    (call,) = bot.send_message.await_args_list
    assert call.args[0] == OTHER_STAFF_ID
    assert "menejerisiz" in call.args[1]

    # Unvon bo'yicha topilgan menejer jadvalga ham yozib qo'yiladi
    (manager,) = await _all(Manager)
    assert (manager.group_id, manager.user_id, manager.username, manager.source) == (
        GROUP_ID, OTHER_STAFF_ID, "menejer_bek", "title",
    )


async def test_title_managers_are_resynced_when_title_is_removed(monkeypatch):
    bot = _make_bot()
    bot.get_chat_administrators.return_value = [_admin(MANAGER, "Menejer")]
    await group_monitor.find_group_managers(bot, GROUP_ID)
    await group_registry.add_manager(GROUP_ID, "@qolda_qoshilgan")

    group_monitor.reset_state()
    bot.get_chat_administrators.return_value = [_admin(MANAGER, None)]
    assert await group_monitor.find_group_managers(bot, GROUP_ID) == {}
    assert [(m.username, m.source) for m in await _all(Manager)] == [("qolda_qoshilgan", "manual")]


async def test_manually_registered_manager_counts_as_staff_and_gets_reminders(monkeypatch):
    """Ichki guruh a'zosi bo'lmagan, lekin `/menejer_qosh` bilan biriktirilgan odam: guruhdagi
    xabari xodim javobi hisoblanadi, Telegram ID'si birinchi xabaridan ma'lum bo'ladi."""
    await group_registry.add_manager(GROUP_ID, "@Tashqi_Menejer")
    outsider = _user(555, "tashqi_menejer", "Tashqi Menejer")
    bot = _make_bot()
    analyze = _fake_analysis(monkeypatch, MessageAnalysis())

    await process_group_message(_make_message(bot, "Assalomu alaykum", outsider, message_id=1))
    assert analyze.await_args.args[1] is True  # xodim sifatida tahlil qilindi
    (manager,) = await _all(Manager)
    assert manager.user_id == 555 and manager.full_name == "Tashqi Menejer"
    assert await _all(StaffMember) == []  # ichki guruh a'zosi emas

    await _customer_asks_nobody(monkeypatch, bot)
    (wait,) = await _waits()
    assert wait.staff_chat_id == 555

    _fake_analysis(monkeypatch, MessageAnalysis())
    await process_group_message(_make_message(bot, "65 mingdan", outsider, message_id=31))
    assert await _waits() == []


async def test_ordinary_customer_message_starts_no_manager_wait(monkeypatch):
    bot = _make_bot()
    bot.get_chat_administrators.return_value = [_admin(MANAGER, "Menejer")]
    await _customer_asks_nobody(monkeypatch, bot, needs_reply=False)
    assert await _waits() == []


async def test_any_staff_answer_closes_manager_wait_but_not_direct_one(monkeypatch):
    bot = _make_bot()
    bot.get_chat_administrators.return_value = [_admin(MANAGER, "meneger")]
    await _customer_asks_nobody(monkeypatch, bot)
    _fake_analysis(monkeypatch, MessageAnalysis())
    # Mijoz boshqa xodimga to'g'ridan-to'g'ri murojaat qilgan -- bu kutish yopilmasligi kerak
    async with async_session_maker() as session:
        session.add(StaffReplyWait(
            group_id=GROUP_ID, staff_chat_id=102, customer_chat_id=1, message_id=1,
            wait_minutes=5, due_at=T0, addressed=True,
        ))
        await session.commit()

    await process_group_message(_make_message(bot, "65 mingdan", STAFF, message_id=31))

    (remaining,) = await _waits()
    assert remaining.staff_chat_id == 102


async def test_staff_promise_does_not_close_manager_wait(monkeypatch):
    bot = _make_bot()
    bot.get_chat_administrators.return_value = [_admin(MANAGER, "Menejer")]
    await _customer_asks_nobody(monkeypatch, bot)
    _fake_analysis(monkeypatch, MessageAnalysis(promised_minutes=10))
    await process_group_message(_make_message(bot, "10 minutda aytaman", STAFF, message_id=31))
    assert len(await _waits()) == 1


# ---------------------------------------------------------------------------
# Ovozli xabar, video, rasm
# ---------------------------------------------------------------------------


def _voice(size=50_000, duration=12):
    return SimpleNamespace(file_id="f1", file_size=size, duration=duration)


def _enable_stt(monkeypatch, transcript):
    transcribe = AsyncMock(return_value=transcript)
    monkeypatch.setattr(group_monitor.settings, "GROUP_STT_ENABLED", True)
    monkeypatch.setattr(group_monitor.settings, "GROUP_STT_MAX_DURATION_SECONDS", 600)
    monkeypatch.setattr(ai_processor.stt, "transcribe_file", transcribe)
    return transcribe


async def test_voice_message_is_transcribed_classified_and_copied_with_its_text(monkeypatch):
    await _add_topics()
    transcribe = _enable_stt(monkeypatch, "uch kundan beri kutyapman hech kim javob bermayapti")
    analyze = _fake_analysis(monkeypatch, MessageAnalysis(category="complaint", about_company=True))
    bot = _make_bot()

    await process_group_message(_make_message(bot, None, CUSTOMER, message_id=7, voice=_voice()))

    bot.download.assert_awaited_once()
    transcribe.assert_awaited_once()
    assert analyze.await_args.args[0] == "uch kundan beri kutyapman hech kim javob bermayapti"
    assert analyze.await_args.kwargs["media"].media_type == "voice"
    # Bitta xabar: ovozli xabarning nusxasi, sarlavha va matni izohida
    bot.send_message.assert_not_called()
    (copy,) = bot.copy_message.await_args_list
    assert copy.args == (HUB_ID, GROUP_ID, 7)
    assert copy.kwargs["message_thread_id"] == 11
    assert "🎙" in copy.kwargs["caption"] and "uch kundan beri kutyapman" in copy.kwargs["caption"]

    (log,) = await _all(MessageLog)
    assert log.media_type == "voice" and log.message_text.startswith("uch kundan beri")


async def test_header_too_long_for_a_caption_is_sent_separately_followed_by_the_media(monkeypatch):
    """Telegram izohi 1024 belgidan oshmaydi: uzun izoh + uzun nutq matni sig'masa sarlavha alohida ketadi."""
    await _add_topics()
    _enable_stt(monkeypatch, "so'z " * 300)
    _fake_analysis(monkeypatch, MessageAnalysis(category="complaint", about_company=True))
    bot = _make_bot()
    await process_group_message(
        _make_message(bot, None, CUSTOMER, message_id=7, video=_voice(), caption="uzun izoh " * 60)
    )

    header = bot.send_message.await_args.args[1]
    assert "🎙" in header and "uzun izoh" in header
    (copy,) = bot.copy_message.await_args_list
    assert copy.args == (HUB_ID, GROUP_ID, 7) and "caption" not in copy.kwargs


async def test_protected_content_still_delivers_header_and_link(monkeypatch):
    """Guruhda "kontentni himoyalash" yoqilgan: media ko'chmaydi, lekin sarlavha va havola yetib boradi."""
    await _add_topics()
    _enable_stt(monkeypatch, "yarmi singan")
    _fake_analysis(monkeypatch, MessageAnalysis(category="complaint", about_company=True))
    bot = _make_bot()
    bot.copy_message = AsyncMock(side_effect=TelegramBadRequest(method=MagicMock(), message="can't be copied"))
    await process_group_message(_make_message(bot, None, CUSTOMER, message_id=7, voice=_voice()))

    (call,) = bot.send_message.await_args_list
    assert "yarmi singan" in call.args[1] and "t.me/c/2222222222/7" in call.args[1]
    (log,) = await _all(MessageLog)
    assert log.forwarded is True


async def test_video_caption_and_speech_are_analyzed_together(monkeypatch):
    _enable_stt(monkeypatch, "mana qarang yarmi singan")
    analyze = _fake_analysis(monkeypatch, MessageAnalysis())
    bot = _make_bot()

    await process_group_message(_make_message(bot, None, CUSTOMER, video=_voice(), caption="Kechagi g'isht"))

    assert analyze.await_args.args[0] == "Kechagi g'isht\nmana qarang yarmi singan"
    assert analyze.await_args.kwargs["media"].media_type == "video"


async def test_voice_reply_to_staff_starts_wait_and_reminder_shows_transcript(monkeypatch):
    _enable_stt(monkeypatch, "narxi qancha bo'ladi")
    bot = _make_bot()
    staff_message = _make_message(bot, "Assalomu alaykum", STAFF, message_id=5)

    await process_group_message(_make_message(bot, None, CUSTOMER, message_id=10, voice=_voice(), reply_to=staff_message))

    (wait,) = await _waits()
    assert wait.staff_chat_id == STAFF_ID and wait.message_text == "narxi qancha bo'ladi"
    await send_due_reminders(bot, now=T0 + timedelta(minutes=5))
    assert "narxi qancha" in bot.send_message.await_args.args[1]


async def test_staff_voice_promise_extends_wait(monkeypatch):
    bot = _make_bot()
    await _customer_asks_staff(bot)
    _enable_stt(monkeypatch, "10 minutda javob beraman")

    await process_group_message(
        _make_message(bot, None, STAFF, message_id=11, voice=_voice(), date=T0 + timedelta(minutes=1))
    )

    (wait,) = await _waits()
    assert wait.promised is True and wait.wait_minutes == 10


@pytest.mark.parametrize("media", [_voice(size=25 * 1024 * 1024), _voice(duration=3600)])
async def test_too_big_or_too_long_media_is_not_downloaded(monkeypatch, media):
    transcribe = _enable_stt(monkeypatch, "matn")
    bot = _make_bot()
    await process_group_message(_make_message(bot, None, CUSTOMER, voice=media))

    bot.download.assert_not_called()
    transcribe.assert_not_called()


async def test_voice_is_ignored_when_stt_disabled(monkeypatch):
    transcribe = AsyncMock(return_value="matn")
    monkeypatch.setattr(group_monitor.settings, "GROUP_STT_ENABLED", False)
    monkeypatch.setattr(ai_processor.stt, "transcribe_file", transcribe)
    bot = _make_bot()
    await process_group_message(_make_message(bot, None, CUSTOMER, voice=_voice()))

    bot.download.assert_not_called()
    transcribe.assert_not_called()


async def test_failed_transcription_does_not_break_processing(monkeypatch):
    _enable_stt(monkeypatch, None)
    bot = _make_bot()
    bot.download = AsyncMock(side_effect=RuntimeError("file is too big"))
    staff_message = _make_message(bot, None, STAFF, message_id=5)

    await process_group_message(_make_message(bot, None, CUSTOMER, message_id=10, voice=_voice(), reply_to=staff_message))

    assert len(await _waits()) == 1


async def test_manager_wait_is_not_opened_if_staff_already_answered_during_analysis(monkeypatch):
    """Mijozning ovozli savoli matnga o'girilayotgan paytda xodim javob berib ulgurdi --
    kechikib ochilgan kutish yolg'on eslatma bermasligi kerak."""
    bot = _make_bot()
    bot.get_chat_administrators.return_value = [_admin(MANAGER, "Menejer")]
    question = _make_message(bot, "Sement qanchadan?", CUSTOMER, message_id=30, date=T0)

    _fake_analysis(monkeypatch, MessageAnalysis())
    await process_group_message(_make_message(bot, "65 mingdan", STAFF, message_id=31, date=T0 + timedelta(seconds=5)))
    _fake_analysis(monkeypatch, MessageAnalysis(needs_reply=True, about_company=True))
    await process_group_message(question)

    assert await _waits() == []


def _photo():
    return [SimpleNamespace(file_id="small", width=320, height=240), SimpleNamespace(file_id="big", width=1280, height=960)]


def _enable_images(monkeypatch):
    monkeypatch.setattr(group_monitor.settings, "GROUP_IMAGE_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(group_monitor.settings, "GROUP_IMAGE_MAX_SIDE", 1024)
    monkeypatch.setattr(ai_processor, "prepare_image", lambda data: b"JPEG")


async def test_customer_photo_is_analyzed_as_image_and_copied_with_its_description(monkeypatch):
    await _add_topics(HubTopic(thread_id=21, name="Mahsulot Sifati va Nuqsonlar"))
    _enable_images(monkeypatch)
    analyze = _fake_analysis(monkeypatch, MessageAnalysis(
        category="complaint", about_company=True, topic="quality", image_note="Singan g'ishtlar taxlami"))
    bot = _make_bot()

    await process_group_message(_make_message(bot, None, CUSTOMER, message_id=7, photo=_photo(), caption="Qarang"))

    # Kerakli aniqlikka yetadigan nusxa yuklab olinadi
    assert bot.download.await_args.args[0].file_id == "big"
    assert analyze.await_args.args[0] == "Qarang"
    assert analyze.await_args.kwargs["media"] == MediaContent("photo", images=(b"JPEG",))
    (copy,) = bot.copy_message.await_args_list
    assert copy.kwargs["message_thread_id"] == 21
    assert "🖼 <i>Singan g'ishtlar taxlami</i>" in copy.kwargs["caption"]
    assert "<blockquote>Qarang</blockquote>" in copy.kwargs["caption"]

    (log,) = await _all(MessageLog)
    assert log.media_type == "photo" and log.message_text == "Qarang\n[rasm: Singan g'ishtlar taxlami]"
    # Keyingi xabar ("mana shunaqa kelibdi") konteksti uchun rasm eslab qolinadi
    assert "rasm yubordi: Singan g'ishtlar taxlami" in group_monitor.take_context(
        _make_message(bot, "x", CUSTOMER), False
    )


async def test_staff_photo_and_disabled_image_analysis_download_nothing(monkeypatch):
    analyze = _fake_analysis(monkeypatch, MessageAnalysis())
    bot = _make_bot()
    await process_group_message(_make_message(bot, None, CUSTOMER, message_id=1, photo=_photo()))  # o'chiq
    _enable_images(monkeypatch)
    await process_group_message(_make_message(bot, None, STAFF, message_id=2, photo=_photo()))  # xodim

    bot.download.assert_not_called()
    assert [call.kwargs["media"] for call in analyze.await_args_list] == [MediaContent("photo"), MediaContent("photo")]


async def test_album_is_analyzed_once_and_its_other_files_follow_to_the_same_topic(monkeypatch):
    await _add_topics()
    _enable_images(monkeypatch)
    analyze = _fake_analysis(monkeypatch, MessageAnalysis(category="complaint", about_company=True))
    bot = _make_bot()

    await process_group_message(
        _make_message(bot, None, CUSTOMER, message_id=1, photo=_photo(), caption="Hammasi singan", media_group_id="a1")
    )
    for message_id in (2, 3):
        await process_group_message(
            _make_message(bot, None, CUSTOMER, message_id=message_id, photo=_photo(), media_group_id="a1")
        )

    analyze.assert_awaited_once()
    assert len(await _all(MessageLog)) == 1
    copies = bot.copy_message.await_args_list
    assert [call.args[2] for call in copies] == [1, 2, 3]
    assert all(call.kwargs["message_thread_id"] == 11 for call in copies)
    assert "caption" in copies[0].kwargs and "caption" not in copies[1].kwargs


async def test_album_that_was_not_forwarded_stays_out_of_the_hub(monkeypatch):
    _enable_images(monkeypatch)
    _fake_analysis(monkeypatch, MessageAnalysis())
    bot = _make_bot()
    for message_id in (1, 2):
        await process_group_message(
            _make_message(bot, None, CUSTOMER, message_id=message_id, photo=_photo(), media_group_id="a2")
        )
    assert _sent_chat_ids(bot) == []
    assert (await _stats())[group_stats.STAT_FILTERED] == 1


async def test_monitoring_disabled_without_hub(monkeypatch):
    monkeypatch.setattr(group_monitor.settings, "FEEDBACK_HUB_GROUP_ID", None)
    bot = _make_bot()
    await _customer_asks_staff(bot)
    assert await _waits() == []
    bot.get_chat_member.assert_not_called()
