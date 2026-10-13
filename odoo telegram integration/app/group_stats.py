"""
Guruh xabarlari statistikasi (2026-10-12): kompaniyaga oid mijoz xabarlari `message_logs`ga
yoziladi, matni saqlanmaydigan narsalar `daily_stats`da sanab boriladi. Kunlik hisobot
(`app/daily_report.py`) shu ikki jadvaldan yig'iladi.

"Kun" -- `REPORT_TIMEZONE` bo'yicha mahalliy kun (00:00 dan 24:00 gacha); bazadagi vaqtlar UTC.

Bu yerdagi yozish funksiyalari xato tashlamaydi: statistika yozilmay qolsa ham guruhni
kuzatish (topiklarga tashlash, javob kutish) to'xtamasligi kerak.
"""

import logging
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import async_session_maker
from app.group_ai import MessageAnalysis
from app.models import DailyStats, MessageLog

logger = logging.getLogger("aiogram_bot")

# `daily_stats.category_name`dagi, toifalardan tashqari sanoqlar
STAT_FILTERED = "Kompaniyaga aloqasiz"  # oddiy suhbat: bazaga yozilmagan, faqat sanalgan
STAT_STAFF = "Xodim xabarlari"
STAT_STOCK = "Tovar yo'q"
STAT_ALERTS = "Javobsiz qolgan savollar"
COUNTER_NAMES = (STAT_FILTERED, STAT_STAFF, STAT_STOCK, STAT_ALERTS)

_MESSAGE_TEXT_LIMIT = 4000
_tz: tzinfo | None = None


def report_tz() -> tzinfo:
    """Hisobot vaqt mintaqasi. Windows'da `tzdata` paketi bo'lmasa IANA nomlari topilmaydi --
    u holda kompyuterning o'z vaqt mintaqasi olinadi."""
    global _tz
    if _tz is None:
        try:
            _tz = ZoneInfo(settings.REPORT_TIMEZONE)
        except (ZoneInfoNotFoundError, ValueError):
            _tz = datetime.now().astimezone().tzinfo
            logger.warning(
                "Vaqt mintaqasi '%s' topilmadi (pip install tzdata) -- kompyuter vaqti ishlatiladi: %s",
                settings.REPORT_TIMEZONE, _tz,
            )
    return _tz


def as_utc(moment: datetime) -> datetime:
    """SQLite vaqtni mintaqasiz qaytaradi -- u holda UTC deb olinadi."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def local_day(moment: datetime | None = None) -> date:
    moment = as_utc(moment) if moment else datetime.now(timezone.utc)
    return moment.astimezone(report_tz()).date()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Mahalliy kunning boshi va oxiri (UTC'da) -- `message_logs.timestamp` bo'yicha tanlash uchun."""
    start = datetime.combine(day, time.min, tzinfo=report_tz())
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=report_tz())
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


async def _add(day: date, name: str, amount: int) -> None:
    increment = (
        update(DailyStats)
        .where(DailyStats.date == day, DailyStats.category_name == name)
        .values(count=DailyStats.count + amount)
    )
    async with async_session_maker() as session:
        if (await session.execute(increment)).rowcount:
            await session.commit()
            return
        session.add(DailyStats(date=day, category_name=name, count=amount))
        try:
            await session.commit()
        except IntegrityError:
            # Shu yozuvni parallel ravishda boshqa xabar yaratib ulgurdi
            await session.rollback()
            await session.execute(increment)
            await session.commit()


async def bump(name: str, moment: datetime | None = None, amount: int = 1) -> None:
    """`name` sanog'ini shu kun uchun oshiradi."""
    try:
        await _add(local_day(moment), name, amount)
    except Exception:
        logger.exception("Statistika sanog'ini yozishda xatolik (%s)", name)


async def log_message(
    message,
    analysis: MessageAnalysis,
    *,
    text: str,
    media_type: str = "text",
    link: str | None = None,
    forwarded: bool = False,
) -> None:
    """Kompaniyaga oid mijoz xabarini `message_logs`ga yozadi."""
    sender = message.from_user
    try:
        async with async_session_maker() as session:
            session.add(
                MessageLog(
                    group_id=message.chat.id,
                    user_id=sender.id,
                    username=sender.username,
                    full_name=sender.full_name,
                    message_id=message.message_id,
                    message_link=link,
                    message_text=text[:_MESSAGE_TEXT_LIMIT] or None,
                    media_type=media_type,
                    category=analysis.category_name,
                    sentiment=analysis.sentiment,
                    is_company_related=True,
                    forwarded=forwarded,
                    timestamp=message.date,
                )
            )
            await session.commit()
    except Exception:
        logger.exception("Xabarni message_logs'ga yozishda xatolik (chat_id=%s)", message.chat.id)
