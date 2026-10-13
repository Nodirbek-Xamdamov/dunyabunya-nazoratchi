"""app.daily_report va app.group_stats -- kunlik statistika: mahalliy kun chegaralari, sanoqlar,
`message_logs`dan yig'ish, HTML hisobot, yarim tundagi ishga tushish."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import daily_report, group_stats
from app.database import async_session_maker
from app.group_ai import CATEGORIES, MessageAnalysis
from app.models import DailyStats, Group, MessageLog

TASHKENT = ZoneInfo("Asia/Tashkent")
DAY = date(2026, 10, 11)
G1, G2 = -1001, -1002


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setattr(daily_report.settings, "REPORT_TIMEZONE", "Asia/Tashkent")
    monkeypatch.setattr(daily_report.settings, "DAILY_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(daily_report.settings, "DAILY_REPORT_ENABLED", True)
    monkeypatch.setattr(daily_report.settings, "DAILY_REPORT_CHAT_ID", None)
    monkeypatch.setattr(daily_report.settings, "DAILY_REPORT_TOPIC_ID", None)
    monkeypatch.setattr(daily_report.settings, "MESSAGE_LOG_RETENTION_DAYS", 180)
    monkeypatch.setattr(daily_report.settings, "FEEDBACK_HUB_GROUP_ID", -100999)
    monkeypatch.setattr(group_stats, "_tz", None)


def _local(hour, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TASHKENT)


def _log(at, category, sentiment="neutral", *, group_id=G1, text="matn", user="Vali", username="vali", **extra):
    return MessageLog(
        group_id=group_id, user_id=1, username=username, full_name=user, message_id=1, message_text=text,
        category=CATEGORIES[category], sentiment=sentiment, timestamp=at.astimezone(timezone.utc), **extra,
    )


async def _seed(*rows):
    async with async_session_maker() as session:
        session.add_all([Group(group_id=G1, title="Chilonzor obyekti"), Group(group_id=G2, title="Sergeli <VIP>")])
        session.add_all(rows)
        await session.commit()


async def _seed_typical_day():
    await _seed(
        _log(_local(0, 0), "price"),  # kunning birinchi daqiqasi -- shu kunga kiradi
        _log(_local(9, 15), "delivery", "negative", text="Uch kundan beri kutyapman <script>alert(1)</script>",
             message_link="https://t.me/c/1/7", media_type="voice"),
        _log(_local(9, 40), "delivery", "negative", group_id=G2, message_link="javascript:alert(1)"),
        _log(_local(9, 50), "quality", "negative", group_id=G2, media_type="photo"),
        _log(_local(14, 5), "positive", "positive", text="Rahmat, tez yetkazdingiz"),
        _log(_local(23, 59), "other"),
        # Qo'shni kunlar: hisobotga kirmasligi kerak (kechagisi faqat taqqoslash uchun)
        _log(_local(23, 59, DAY - timedelta(days=1)), "delivery", "negative"),
        _log(_local(0, 0, DAY + timedelta(days=1)), "price"),
        DailyStats(date=DAY, category_name=group_stats.STAT_FILTERED, count=1284),
        DailyStats(date=DAY, category_name=group_stats.STAT_ALERTS, count=3),
        DailyStats(date=DAY - timedelta(days=1), category_name=group_stats.STAT_ALERTS, count=5),
    )


# --- Kun chegaralari va sanoqlar -------------------------------------------------------------------


def test_report_day_is_local_day_in_tashkent():
    start, end = group_stats.day_bounds(DAY)
    assert start == datetime(2026, 10, 10, 19, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 11, 19, 0, tzinfo=timezone.utc)
    # UTC bo'yicha hali 10-oktabr, Toshkentda esa allaqachon 11-oktabr
    assert group_stats.local_day(datetime(2026, 10, 10, 19, 30, tzinfo=timezone.utc)) == DAY
    assert group_stats.local_day(datetime(2026, 10, 10, 18, 59, tzinfo=timezone.utc)) == date(2026, 10, 10)


def test_unknown_timezone_falls_back_to_computer_time(monkeypatch):
    monkeypatch.setattr(group_stats.settings, "REPORT_TIMEZONE", "Mars/Olympus")
    assert group_stats.report_tz() is not None


async def test_bump_counts_per_local_day_and_name():
    moment = datetime(2026, 10, 10, 19, 30, tzinfo=timezone.utc)  # Toshkentda 11-oktabr 00:30
    await group_stats.bump(group_stats.STAT_FILTERED, moment)
    await group_stats.bump(group_stats.STAT_FILTERED, moment)
    await group_stats.bump(group_stats.STAT_STOCK, moment, amount=3)
    await group_stats.bump(group_stats.STAT_FILTERED, moment - timedelta(hours=1))

    async with async_session_maker() as session:
        rows = {(r.date, r.category_name): r.count for r in (await session.execute(select(DailyStats))).scalars()}
    assert rows == {
        (DAY, group_stats.STAT_FILTERED): 2,
        (DAY, group_stats.STAT_STOCK): 3,
        (date(2026, 10, 10), group_stats.STAT_FILTERED): 1,
    }


async def test_log_message_stores_category_name_and_sentiment():
    message = SimpleNamespace(
        chat=SimpleNamespace(id=G1), message_id=9, date=_local(10).astimezone(timezone.utc),
        from_user=SimpleNamespace(id=5, username="vali", full_name="Vali"),
    )
    await group_stats.log_message(
        message, MessageAnalysis(category="praise", about_company=True, topic="delivery"), text="Rahmat",
        media_type="voice", link="https://t.me/c/1/9", forwarded=True,
    )
    async with async_session_maker() as session:
        (log,) = (await session.execute(select(MessageLog))).scalars().all()
    assert (log.category, log.sentiment, log.media_type, log.forwarded) == ("Ijobiy Fikrlar", "positive", "voice", True)
    assert log.is_company_related is True


# --- Ma'lumot yig'ish ------------------------------------------------------------------------------


async def test_collect_groups_last_24_hours_of_complaints_by_category():
    await _seed_typical_day()
    data = await daily_report.collect_report_data(DAY)

    assert [row.name for row in data.categories] == list(CATEGORIES.values())
    by_name = {row.name: (row.total, row.complaints, row.positive) for row in data.categories}
    assert by_name == {
        "Dastavka va Logistika": (2, 2, 0),
        "Mahsulot Sifati va Nuqsonlar": (1, 1, 0),
        "Komplektatsiya va Hajm": (0, 0, 0),
        "Xodimlar va Xizmat Ko'rsatish": (0, 0, 0),
        "Narx va To'lovlar": (1, 0, 0),
        "Qaytarish va Kafolat": (0, 0, 0),
        "Ijobiy Fikrlar": (1, 0, 1),
        "Boshqa": (1, 0, 0),
    }
    assert (data.total, data.complaints_total, data.positive_total, data.filtered) == (6, 3, 1, 1284)
    assert data.counters[group_stats.STAT_ALERTS] == 3 and data.counters[group_stats.STAT_STOCK] == 0

    # Jadvalga faqat shikoyatlar va ijobiy fikrlar chiqadi (oddiy savollar emas), vaqt mahalliy
    listed = {row.name: [(m.at.strftime("%H:%M"), m.group) for m in row.messages] for row in data.categories}
    assert listed == {
        "Dastavka va Logistika": [("09:15", "Chilonzor obyekti"), ("09:40", "Sergeli <VIP>")],
        "Mahsulot Sifati va Nuqsonlar": [("09:50", "Sergeli <VIP>")],
        "Komplektatsiya va Hajm": [],
        "Xodimlar va Xizmat Ko'rsatish": [],
        "Narx va To'lovlar": [],
        "Qaytarish va Kafolat": [],
        "Ijobiy Fikrlar": [("14:05", "Chilonzor obyekti")],
        "Boshqa": [],
    }


async def test_empty_day_is_marked_empty():
    data = await daily_report.collect_report_data(DAY)
    assert data.is_empty and data.total == 0


# --- HTML -------------------------------------------------------------------------------------------


async def test_html_report_is_one_plain_table_split_by_category():
    await _seed_typical_day()
    page = daily_report.render_report_html(await daily_report.collect_report_data(DAY))

    assert page.startswith("<!doctype html>") and "2026-yil 11-oktabr, yakshanba" in page
    # Bitta jadval; karta, diagramma, skript yo'q
    assert page.count("<table>") == 1 and "<script" not in page and "tile" not in page
    # Har bir toifa -- sarlavha qatori (nomi va soni), o'z tartibida
    headers = [line for line in page.splitlines() if line.startswith('<tr class="cat">')]
    assert [h.replace("&#x27;", "'") for h in headers] == [
        '<tr class="cat"><td colspan="4">Dastavka va Logistika — 2</td></tr>',
        '<tr class="cat"><td colspan="4">Mahsulot Sifati va Nuqsonlar — 1</td></tr>',
        '<tr class="cat"><td colspan="4">Komplektatsiya va Hajm — 0</td></tr>',
        '<tr class="cat"><td colspan="4">Xodimlar va Xizmat Ko\'rsatish — 0</td></tr>',
        '<tr class="cat"><td colspan="4">Narx va To\'lovlar — 0</td></tr>',
        '<tr class="cat"><td colspan="4">Qaytarish va Kafolat — 0</td></tr>',
        '<tr class="cat"><td colspan="4">Ijobiy Fikrlar — 1</td></tr>',
        '<tr class="cat"><td colspan="4">Boshqa — 0</td></tr>',
    ]
    # Xabar o'z toifasi ostida turadi
    delivery, quality = page.index("Dastavka va Logistika"), page.index("Mahsulot Sifati")
    assert delivery < page.index("09:15") < page.index("09:40") < quality < page.index("09:50")
    assert page.count('<td class="time">') == 4

    # Mijoz yozgan matn va guruh nomi hujjatga kod bo'lib tushmaydi
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "Sergeli &lt;VIP&gt;" in page
    assert 'href="https://t.me/c/1/7"' in page and "javascript:alert" not in page


async def test_html_report_for_empty_day_has_only_category_rows():
    page = daily_report.render_report_html(await daily_report.collect_report_data(DAY))
    assert page.count('<tr class="cat">') == 8 and '<td class="time">' not in page


# --- Faylga yozish, yuborish, yarim tun -------------------------------------------------------------


async def test_generate_report_writes_file_and_saves_category_counts(tmp_path):
    await _seed_typical_day()
    path, data = await daily_report.generate_report(DAY)
    path2, _ = await daily_report.generate_report(DAY)  # qayta tuzilsa sanoqlar ikkilanmaydi

    assert path == path2 == tmp_path / "reports" / "stats_2026-10-11.html"
    assert "Kunlik hisobot" in path.read_text(encoding="utf-8")
    async with async_session_maker() as session:
        rows = (await session.execute(select(DailyStats).where(DailyStats.date == DAY))).scalars().all()
    counts = {row.category_name: row.count for row in rows}
    assert len(rows) == len(counts) == 10  # 8 toifa + 2 ta sanoq
    assert counts["Dastavka va Logistika"] == 2 and counts["Boshqa"] == 1 and counts["Komplektatsiya va Hajm"] == 0
    assert counts[group_stats.STAT_FILTERED] == 1284


async def test_report_is_sent_to_admin_chat_only_when_configured(monkeypatch):
    await _seed_typical_day()
    bot = MagicMock()
    bot.send_document = AsyncMock()

    await daily_report.run_daily_report(bot, DAY)
    bot.send_document.assert_not_called()

    monkeypatch.setattr(daily_report.settings, "DAILY_REPORT_CHAT_ID", -100777)
    monkeypatch.setattr(daily_report.settings, "DAILY_REPORT_TOPIC_ID", 12)
    path = await daily_report.run_daily_report(bot, DAY)

    call = bot.send_document.await_args
    assert call.args[0] == -100777 and call.kwargs["message_thread_id"] == 12
    assert call.args[1].path == path and call.args[1].filename == "stats_2026-10-11.html"
    assert "Shikoyatlar: 3" in call.kwargs["caption"] and "filtrlangan): 1284" in call.kwargs["caption"]

    # Hech narsa bo'lmagan kun: fayl yoziladi, lekin chatga yuborilmaydi
    bot.send_document.reset_mock()
    quiet_day = DAY + timedelta(days=40)
    assert (await daily_report.run_daily_report(bot, quiet_day)).exists()
    bot.send_document.assert_not_called()


async def test_old_logs_are_deleted_after_retention_period(monkeypatch):
    await _seed(_log(_local(12, day=DAY - timedelta(days=200)), "price"), _log(_local(12), "price"))
    assert await daily_report.delete_old_logs(DAY) == 1
    monkeypatch.setattr(daily_report.settings, "MESSAGE_LOG_RETENTION_DAYS", 0)
    assert await daily_report.delete_old_logs(DAY + timedelta(days=5000)) == 0


def test_seconds_until_midnight():
    assert daily_report.seconds_until_midnight(_local(23, 59)) == 60
    assert daily_report.seconds_until_midnight(_local(0, 0)) == 24 * 3600
    assert daily_report.seconds_until_midnight(_local(12, 30)) == 11.5 * 3600


async def test_midnight_loop_writes_report_for_the_day_that_just_ended(monkeypatch):
    yesterday = datetime.now(TASHKENT).date() - timedelta(days=1)
    await _seed(_log(_local(15, day=yesterday), "delivery", "negative"))
    monkeypatch.setattr(daily_report.settings, "DAILY_REPORT_CHAT_ID", -100777)
    bot = MagicMock()
    bot.send_document = AsyncMock()
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(daily_report.asyncio, "sleep", fake_sleep)
    # Ishga tushganda kechagi hisobot allaqachon bor -- qayta tuzilmaydi, faqat yarim tun kutiladi
    daily_report.report_path(yesterday).parent.mkdir(parents=True)
    daily_report.report_path(yesterday).write_text("eski", encoding="utf-8")

    with pytest.raises(asyncio.CancelledError):
        await daily_report.daily_report_loop(bot)

    assert 0 < sleeps[0] <= 24 * 3600 + 2
    assert "Kunlik hisobot" in daily_report.report_path(yesterday).read_text(encoding="utf-8")
    bot.send_document.assert_awaited_once()


async def test_missed_report_is_caught_up_on_start_and_loop_can_be_disabled(monkeypatch):
    yesterday = datetime.now(TASHKENT).date() - timedelta(days=1)
    await _seed(_log(_local(15, day=yesterday), "delivery", "negative"))

    async def stop(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(daily_report.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        await daily_report.daily_report_loop(None)
    assert daily_report.report_path(yesterday).exists()

    daily_report.report_path(yesterday).unlink()
    monkeypatch.setattr(daily_report.settings, "DAILY_REPORT_ENABLED", False)
    await daily_report.daily_report_loop(None)  # darhol qaytadi
    assert not daily_report.report_path(yesterday).exists()
