"""app.monitor_sla -- guruh menejerini aniqlash va "5 daqiqa qoidasi"."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramForbiddenError

from app import monitor_db, monitor_sla
from app.monitor_db import ClassificationResult, ManagerCheck
from tests.monitor_helpers import ADMIN_ID, CHAT, DAY, MANAGER_ID, T0, add_group, add_message, all_rows, classify


def _admin(user_id, title=None, username=None, name="Ism", is_bot=False):
    user = SimpleNamespace(id=user_id, username=username, full_name=name, is_bot=is_bot)
    return SimpleNamespace(user=user, custom_title=title, status="administrator")


def _bot(admins):
    bot = MagicMock()
    bot.get_chat_administrators = AsyncMock(return_value=admins)
    return bot


async def _ask(message_id, minutes=0.0, text="Sement qanchadan?"):
    """Mijoz so'rovi: yoziladi, "menejer javobi kerak" deb tasniflanadi, tekshiruv ochiladi."""
    pk = await add_message(message_id, text, minutes=minutes)
    await classify(pk, needs_manager=True)
    await monitor_sla.open_checks([pk])
    return pk


async def _manager_writes(message_id, minutes, sender_id=MANAGER_ID):
    await add_message(message_id, "65 mingdan", sender_id=sender_id, is_staff=True, name="Bek", minutes=minutes)


async def _checks():
    return await all_rows(ManagerCheck)


@pytest.mark.parametrize("title", ["Meneger", "MENEJER", "manager", "Savdo menejeri", "Старший менеджер", "Menedjer"])
def test_manager_is_admin_whose_title_contains_manager_word(title):
    admins = [_admin(1, "Direktor"), _admin(2, title, "bek"), _admin(3, None), _admin(4, "manager bot", is_bot=True)]
    assert monitor_sla.find_managers(admins) == [(2, "bek", "Ism")]


async def test_group_admins_and_manager_are_fetched_once_and_stored():
    bot = _bot([_admin(ADMIN_ID), _admin(MANAGER_ID, "Menejer", "menejer_bek", "Bek"), _admin(9, is_bot=True)])
    group = await monitor_sla.group_for_chat(bot, CHAT, "Chilonzor")
    await monitor_sla.group_for_chat(bot, CHAT, "Chilonzor")

    bot.get_chat_administrators.assert_awaited_once_with(CHAT)
    assert group.admin_ids == {ADMIN_ID, MANAGER_ID}
    assert (group.manager_ids, group.manager_user_id, group.manager_username) == ((MANAGER_ID,), MANAGER_ID, "@menejer_bek")

    # Admin o'zgardi -- keyingi xabarda qayta olinadi
    bot.get_chat_administrators.return_value = [_admin(ADMIN_ID)]
    await monitor_sla.admins_changed(CHAT)
    group = await monitor_sla.group_for_chat(bot, CHAT, "Chilonzor")
    assert group.manager_ids == () and group.manager_username is None


async def test_group_without_access_is_still_registered_and_not_retried_every_message():
    bot = MagicMock()
    bot.get_chat_administrators = AsyncMock(side_effect=TelegramForbiddenError(method=MagicMock(), message="kicked"))
    group = await monitor_sla.group_for_chat(bot, CHAT, "Chilonzor")
    monitor_sla._cache.clear()
    await monitor_sla.group_for_chat(bot, CHAT, "Chilonzor")

    assert group.admin_ids == frozenset()
    bot.get_chat_administrators.assert_awaited_once()


async def test_stale_groups_are_refreshed_by_the_minute_task():
    await monitor_db.upsert_group(CHAT, "Chilonzor")
    bot = _bot([_admin(MANAGER_ID, "Menejer", "menejer_bek")])
    await monitor_sla.sla_tick(bot)
    await monitor_sla.sla_tick(bot)  # endi yangi -- qayta so'ralmaydi

    bot.get_chat_administrators.assert_awaited_once_with(CHAT)
    assert (await monitor_db.get_group(CHAT)).manager_username == "@menejer_bek"


async def test_manager_reply_within_deadline_is_answered():
    await add_group()
    await _ask(1)
    (check,) = await _checks()
    assert check.status == "pending" and check.deadline == T0 + timedelta(minutes=5)

    await _manager_writes(2, minutes=4)
    assert await monitor_db.resolve_checks(T0 + timedelta(minutes=4, seconds=5)) == (1, 0)
    (check,) = await _checks()
    assert (check.status, check.answered_at) == ("answered", T0 + timedelta(minutes=4))
    assert await monitor_db.missed_requests(DAY) == []


async def test_no_manager_reply_is_missed_with_manager_username(monkeypatch):
    monkeypatch.setattr(monitor_db.settings, "SLA_REQUIRE_MANAGER", True)
    await add_group()
    await _ask(1)
    # Menejer emas, boshqa admin yozdi -- bu guruhda menejer bor, uning javobi kerak
    await _manager_writes(2, minutes=1, sender_id=ADMIN_ID)
    await _manager_writes(3, minutes=6)  # menejer kech yozdi

    assert await monitor_db.resolve_checks(T0 + timedelta(minutes=5)) == (0, 0)  # muddat endi tugadi, biroz kutadi
    assert await monitor_db.resolve_checks(T0 + timedelta(minutes=5, seconds=20)) == (0, 1)
    (missed,) = await monitor_db.missed_requests(DAY)
    assert (missed.group, missed.customer, missed.summary, missed.manager) == (
        "Chilonzor obyekti", "Vali Mijoz (@vali)", "Mijoz narx so'radi", "@menejer_bek",
    )
    assert missed.at.strftime("%H:%M") == "10:00"
    assert (await monitor_db.compute_day_stats(DAY)).missed == 1


async def test_group_without_manager_accepts_any_admin_reply_and_reports_missed_as_no_manager():
    await add_group(managers=())
    await _ask(1)
    await _manager_writes(2, minutes=2, sender_id=ADMIN_ID)
    await _ask(3, minutes=30)

    await monitor_db.resolve_checks(T0 + timedelta(minutes=40))
    assert [check.status for check in await _checks()] == ["answered", "missed"]
    (missed,) = await monitor_db.missed_requests(DAY)
    assert missed.manager is None


async def test_consecutive_requests_merge_into_one_check_from_the_first_message():
    await add_group()
    await _ask(1)
    await _ask(2, minutes=2)
    await _ask(3, minutes=4.5)
    (check,) = await _checks()
    assert check.first_message_id == 1 and check.deadline == T0 + timedelta(minutes=5)

    await _ask(4, minutes=5.5)  # muddat tugagan -- yangi tekshiruv
    assert [c.first_message_id for c in await _checks()] == [1, 4]


async def test_request_after_manager_answer_starts_a_new_check():
    await add_group()
    await _ask(1)
    await _manager_writes(2, minutes=1)
    await _ask(3, minutes=2)

    assert [c.first_message_id for c in await _checks()] == [1, 3]
    await monitor_db.resolve_checks(T0 + timedelta(minutes=10))
    assert [c.status for c in await _checks()] == ["answered", "missed"]


async def test_check_opened_late_still_sees_reply_that_already_arrived():
    """Tasniflash kechikdi (yoki bot o'chiq edi): tekshiruv xabar vaqtidan hisoblanadi va shu
    orada kelgan menejer javobini ko'radi."""
    await add_group()
    pk = await add_message(1)
    await _manager_writes(2, minutes=3)
    await classify(pk, needs_manager=True)
    await monitor_sla.open_checks([pk])

    assert await monitor_db.resolve_checks(T0 + timedelta(hours=2)) == (1, 0)


OTHER_ID = 300


async def _other_writes(message_id, minutes, text="Ertaga olib boramiz", *, reply_to=None, request=False, rule=False):
    """Mijozning o'zi emas, guruhdagi boshqa (xodim deb belgilanmagan) odam yozadi."""
    pk = await add_message(message_id, text, sender_id=OTHER_ID, name="Sardor", username=None, minutes=minutes,
                           reply_to=reply_to)
    if rule:  # "+", stiker va h.k. -- qoida bilan "aloqasiz" deb ajratiladi
        await monitor_db.save_classifications([ClassificationResult(pk, False, None, None, None, False)], "rule")
    else:
        await classify(pk, needs_manager=request)
    return pk


async def test_anyone_answering_the_customer_counts_when_manager_is_not_required():
    await add_group()
    await _ask(1)
    await add_message(2, "Tezroq javob bering", minutes=1)  # mijozning o'zi yozdi -- javob emas
    await _other_writes(3, minutes=2, text="Menga ham sement kerak", request=True)  # boshqaning savoli -- javob emas
    await _other_writes(4, minutes=3, text="+", rule=True)  # reply emas "+" -- javob emas
    assert await monitor_db.resolve_checks(T0 + timedelta(minutes=6)) == (0, 1)

    await _ask(10, minutes=30)
    await _other_writes(11, minutes=31, text="+", rule=True, reply_to=10)  # mijoz xabariga reply qilingan "+"
    await _ask(20, minutes=60)
    await _other_writes(21, minutes=62)  # boshqa odamning so'rov bo'lmagan xabari
    await monitor_db.resolve_checks(T0 + timedelta(minutes=90))
    assert [(c.first_message_id, c.status) for c in await _checks()] == [(1, "missed"), (10, "answered"), (20, "answered")]


async def test_check_waits_for_others_unclassified_message_before_missing(monkeypatch):
    await add_group()
    await _ask(1)
    pending = await add_message(2, "Hozir aniqlab beraman", sender_id=OTHER_ID, name="Sardor", minutes=2)
    assert await monitor_db.resolve_checks(T0 + timedelta(minutes=6)) == (0, 0)  # u javobmi -- hali noma'lum
    await classify(pending)
    assert await monitor_db.resolve_checks(T0 + timedelta(minutes=7)) == (1, 0)

    await _ask(10, minutes=30)
    await add_message(11, "?", sender_id=OTHER_ID, name="Sardor", minutes=31)  # tasniflanmay qoldi
    assert await monitor_db.resolve_checks(T0 + timedelta(minutes=40)) == (0, 0)
    assert await monitor_db.resolve_checks(T0 + timedelta(minutes=50)) == (0, 1)  # 15 daqiqadan keyin baribir yopiladi

    monkeypatch.setattr(monitor_db.settings, "SLA_REQUIRE_MANAGER", True)
    await _ask(20, minutes=60)
    await add_message(21, "?", sender_id=OTHER_ID, name="Sardor", minutes=61)
    assert await monitor_db.resolve_checks(T0 + timedelta(minutes=66)) == (0, 1)  # menejer kerak -- kutilmaydi


async def test_missed_request_shows_who_answered_later_and_when():
    await add_group()
    await _ask(1)
    await monitor_db.resolve_checks(T0 + timedelta(minutes=6))
    (missed,) = await monitor_db.missed_requests(DAY)
    assert missed.later_answer is None

    await add_message(2, "65 mingdan", sender_id=MANAGER_ID, is_staff=True, name="Bek", username="menejer_bek", minutes=25)
    (missed,) = await monitor_db.missed_requests(DAY)
    assert missed.later_answer == "Bek (@menejer_bek), 25 daqiqadan keyin"
    assert monitor_db._after_text(timedelta(hours=2, minutes=9, seconds=30)) == "2 soat 10 daqiqadan keyin"
    assert monitor_db._after_text(timedelta(hours=3)) == "3 soatdan keyin"


@pytest.mark.parametrize("name, username, expected", [
    ("dunyabunya Santexnika", None, True),
    ("Ali", "Dunyabunya_035", True),
    ("Vali Mijoz", "vali", False),
    (None, None, False),
])
def test_company_accounts_are_staff_by_name(name, username, expected):
    assert monitor_sla.is_staff_name(name, username) is expected


async def test_work_hours_limit_is_off_by_default_and_respected_when_set(monkeypatch):
    morning = datetime(2026, 10, 13, 2, 0, tzinfo=timezone.utc)  # Toshkentda 07:00
    assert monitor_sla.in_work_hours(morning) is True

    monkeypatch.setattr(monitor_sla.settings, "SLA_WORK_HOURS", "09:00-19:00")
    assert monitor_sla.in_work_hours(morning) is False
    assert monitor_sla.in_work_hours(T0) is True  # 10:00

    await add_group()
    await _ask(1, minutes=-180)  # 07:00 -- ish vaqtidan tashqari
    await _ask(2)
    assert [c.first_message_id for c in await _checks()] == [2]


async def test_sla_minutes_come_from_config(monkeypatch):
    monkeypatch.setattr(monitor_sla.settings, "SLA_MINUTES", 1)
    await add_group()
    await _ask(1)
    (check,) = await _checks()
    assert check.deadline == T0 + timedelta(minutes=1)
