"""
"5 daqiqa qoidasi" va guruh adminlari/menejerlari (2026-10-13).

Menejer: guruh adminlari orasida unvoni (custom title) `MANAGER_TITLES`dagi so'zlardan birini
o'z ichiga olgan odam (katta-kichik harf farqsiz), `getChatAdministrators` orqali olinadi va
`groups` jadvalida saqlanadi. Kuniga bir marta va admin o'zgarganda yangilanadi. Xodimlar
(`is_staff`) -- guruh adminlari va ismida `STAFF_NAME_KEYWORDS` so'zi bor akkauntlar.

Qoida: mijoz kompaniyaga murojaat qilsa (tasniflovchi `needs_manager` degan xabar) tekshiruv
ochiladi, muddati -- xabar vaqtidan `SLA_MINUTES` daqiqa. Shu vaqt ichida javob yozilsa --
"answered", yozilmasa -- "missed" (hisobotdagi "Javobsiz so'rovlar"ga tushadi). Ketma-ket kelgan
so'rovlar bitta tekshiruvga birlashadi. Kimning javobi hisoblanishi (`monitor_db.answers_query`):
`SLA_REQUIRE_MANAGER=false` (hozircha) -- mijozga kimdir javob bersa yetarli; true -- menejer
(guruhda menejer bo'lmasa -- istalgan xodim).

Tekshiruvlar bazada turadi va har daqiqa `sla_loop` tomonidan yopiladi -- bot qayta ishga
tushsa ham yo'qolmaydi: javob bor-yo'qligi `messages` jadvalidagi vaqtlar bo'yicha aniqlanadi.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramNotFound

from app import monitor_db
from app.config import settings
from app.monitor_db import Group

logger = logging.getLogger(__name__)

_ADMINS_MAX_AGE = timedelta(hours=24)
_CACHE_SECONDS = 300
_RETRY_SECONDS = 600
_GONE_RETRY_SECONDS = 24 * 3600
_REFRESH_PER_TICK = 5

# chat_id -> (guruh, keshning amal qilish vaqti)
_cache: dict[int, tuple[Group, float]] = {}
# chat_id -> adminlarni olish xato bergach qachongacha qayta urinilmaydi
_retry_after: dict[int, float] = {}


def reset_state() -> None:
    _cache.clear()
    _retry_after.clear()


def manager_titles() -> list[str]:
    return [part.strip().lower() for part in settings.MANAGER_TITLES.split(",") if part.strip()]


def is_staff_name(name: str | None, username: str | None) -> bool:
    """Ismi yoki username'ida `STAFF_NAME_KEYWORDS`dagi so'z bor akkaunt -- kompaniya xodimi."""
    keywords = [part.strip().lower() for part in settings.STAFF_NAME_KEYWORDS.split(",") if part.strip()]
    text = f"{name or ''} {username or ''}".lower()
    return any(keyword in text for keyword in keywords)


def find_managers(admins) -> list[tuple[int, str | None, str | None]]:
    """Adminlar ro'yxatidan menejerlarni ajratadi: (user_id, username, ism)."""
    titles = manager_titles()
    managers = []
    for admin in admins:
        title = (getattr(admin, "custom_title", None) or "").lower()
        if not admin.user.is_bot and any(word in title for word in titles):
            managers.append((admin.user.id, admin.user.username, admin.user.full_name))
    return managers


async def refresh_group(bot: Bot, chat_id: int) -> Group | None:
    """Guruh adminlari va menejerlarini Telegram'dan olib bazaga yozadi."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except TelegramAPIError as exc:
        # Bot guruhdan chiqarilgan bo'lsa (Forbidden / chat not found) har 10 daqiqada emas, kuniga bir marta urinadi
        gone = isinstance(exc, (TelegramForbiddenError, TelegramBadRequest, TelegramNotFound))
        _retry_after[chat_id] = time.monotonic() + (_GONE_RETRY_SECONDS if gone else _RETRY_SECONDS)
        logger.warning("Guruh adminlarini olib bo'lmadi (chat_id=%s): %s", chat_id, exc)
        return None
    admin_ids = {admin.user.id for admin in admins if not admin.user.is_bot}
    managers = find_managers(admins)
    group = await monitor_db.save_group_admins(chat_id, admin_ids, managers)
    if group is not None:
        _cache[chat_id] = (group, time.monotonic() + _CACHE_SECONDS)
        logger.info(
            "Guruh adminlari yangilandi: chat_id=%s, admin %s ta, menejer: %s",
            chat_id, len(admin_ids), group.manager_username or "yo'q",
        )
    return group


def _needs_refresh(group: Group) -> bool:
    if _retry_after.get(group.chat_id, 0) > time.monotonic():
        return False
    return group.admins_checked_at is None or monitor_db.utc_now() - group.admins_checked_at > _ADMINS_MAX_AGE


async def group_for_chat(bot: Bot, chat_id: int, title: str | None) -> Group:
    """Guruhning bazadagi yozuvi (yo'q bo'lsa yaratiladi). Adminlari hali olinmagan yoki bir
    kundan eski bo'lsa Telegram'dan yangilanadi. Natija qisqa muddat xotirada keshlanadi."""
    cached = _cache.get(chat_id)
    if cached and cached[1] > time.monotonic() and cached[0].title == title:
        return cached[0]
    group = await monitor_db.upsert_group(chat_id, title)
    if _needs_refresh(group):
        group = await refresh_group(bot, chat_id) or group
    _cache[chat_id] = (group, time.monotonic() + _CACHE_SECONDS)
    return group


async def admins_changed(chat_id: int) -> None:
    """Guruhda admin qo'shildi/olindi yoki unvoni o'zgardi -- keyingi xabarda qayta olinadi."""
    _cache.pop(chat_id, None)
    _retry_after.pop(chat_id, None)
    await monitor_db.invalidate_group_admins(chat_id)


def in_work_hours(moment: datetime) -> bool:
    """`SLA_WORK_HOURS` ("09:00-19:00") ichidami. Bo'sh bo'lsa -- cheklov yo'q (24/7)."""
    window = settings.SLA_WORK_HOURS.strip()
    if not window:
        return True
    try:
        start, end = (datetime.strptime(part.strip(), "%H:%M").time() for part in window.split("-"))
    except ValueError:
        logger.warning("SLA_WORK_HOURS noto'g'ri yozilgan: %r (masalan 09:00-19:00)", window)
        return True
    local = moment.astimezone(monitor_db.local_tz()).time()
    return start <= local < end if start <= end else (local >= start or local < end)


async def open_checks(message_pks: list[int]) -> None:
    """Menejer javobi kerak bo'lgan xabarlar uchun tekshiruv ochadi (ish vaqtidan tashqarisi uchun emas)."""
    for message_pk in message_pks:
        if settings.SLA_WORK_HOURS.strip():
            async with monitor_db.session_maker() as session:
                message = await session.get(monitor_db.Message, message_pk)
            if message is None or not in_work_hours(message.sent_at_utc):
                continue
        if await monitor_db.open_check(message_pk, settings.SLA_MINUTES):
            logger.info("Menejer javobi kutilmoqda: xabar=%s, %s daqiqa", message_pk, settings.SLA_MINUTES)


async def sla_tick(bot: Bot) -> None:
    answered, missed = await monitor_db.resolve_checks()
    if answered or missed:
        logger.info("5 daqiqa qoidasi: javob berilgan %s, javobsiz %s", answered, missed)
    # Kuniga bir marta: adminlari eskirgan guruhlarni oz-ozdan yangilab chiqadi
    stale = await monitor_db.stale_group_ids(monitor_db.utc_now() - _ADMINS_MAX_AGE, _REFRESH_PER_TICK)
    for chat_id in stale:
        if _retry_after.get(chat_id, 0) <= time.monotonic():
            await refresh_group(bot, chat_id)


async def sla_loop(bot: Bot) -> None:
    """Bot jarayonida BITTA nusxada ishlaydi: har daqiqa tekshiruvlarni yopadi."""
    while True:
        try:
            await sla_tick(bot)
        except Exception:
            logger.exception("5 daqiqa qoidasini tekshirishda xatolik")
        await asyncio.sleep(60)
