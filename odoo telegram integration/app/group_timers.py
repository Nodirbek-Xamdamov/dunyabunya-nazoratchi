"""
Mijoz savoliga javob kutish, `GROUP_ALERT_MODE=group` rejimi (2026-10-12).

Har bir guruh uchun BITTA kutish (`group_inquiry_timers`):

  1. Mijoz dunyabunyaga savol/so'rov yozdi (model `needs_reply` dedi) -> kutish boshlanadi,
     muddati `STAFF_REPLY_TIMEOUT_MINUTES` (5 daqiqa). Kutish ketayotgan bo'lsa muddat
     BIRINCHI javobsiz xabardan hisoblanaveradi -- keyingi savollar uni surmaydi.
  2. Shu guruhga ISTALGAN xodim yozdi -> kutish bekor bo'ladi. Xodim "10 minutda javob
     beramiz" degan bo'lsa -- kutish o'sha muddatga suriladi.
  3. Muddat o'tdi, hech bir xodim yozmadi -> guruhning O'ZIGA alohida xabar (reply/iqtibossiz)
     yoziladi: shu guruhga biriktirilgan barcha menejerlar @username bilan belgilanadi. Bot
     mijoz guruhiga yozadigan YAGONA xabar shu.
     Guruhga menejer biriktirilmagan bo'lsa (yoki bot guruhga yoza olmasa) guruhga hech narsa
     yozilmaydi -- ogohlantirish ichki guruhning "Javobsiz savollar" topigiga tushadi.
  4. Ogohlantirishdan keyin xodim javob yozmaguncha shu guruhda keyingisi
     `GROUP_ALERT_REPEAT_MINUTES`dan oldin yuborilmaydi.

Kutishlar bazada turadi va `staff_reply_reminder_loop` (group_monitor) har
`STAFF_REPLY_CHECK_INTERVAL_SECONDS`da tekshiradi -- bot qayta ishga tushsa ham yo'qolmaydi.

`GROUP_ALERT_MODE=dm` rejimida bu modul ishlatilmaydi: u yerda kutish har bir xodim uchun
alohida (`staff_reply_waits`) va eslatma xodimning shaxsiy chatiga boradi.
"""

import html
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from sqlalchemy import delete, select

from app import group_stats
from app.config import settings
from app.database import async_session_maker
from app.group_registry import ManagerInfo
from app.models import InquiryTimer

logger = logging.getLogger("aiogram_bot")

_SNIPPET_LIMIT = 300


async def start_timer(message: Message, text: str | None, link: str | None = None) -> bool:
    """Mijoz savoli bo'yicha guruhning kutishini boshlaydi. Yangi kutish ochilsa True."""
    minutes = settings.STAFF_REPLY_TIMEOUT_MINUTES
    async with async_session_maker() as session:
        timer = (
            await session.execute(select(InquiryTimer).where(InquiryTimer.group_id == message.chat.id))
        ).scalar_one_or_none()
        if timer is not None:
            if timer.notified_at is None:
                return False  # kutish ketyapti
            repeat_after = group_stats.as_utc(timer.notified_at) + timedelta(minutes=settings.GROUP_ALERT_REPEAT_MINUTES)
            if message.date < repeat_after:
                return False  # menejerlar yaqinda belgilangan, hali hech kim javob yozmadi
        timer = timer or InquiryTimer(group_id=message.chat.id)
        timer.group_title = message.chat.title
        timer.customer_chat_id = message.from_user.id
        timer.customer_name = message.from_user.full_name
        timer.message_id = message.message_id
        timer.thread_id = message.message_thread_id if getattr(message, "is_topic_message", False) else None
        timer.message_text = text
        timer.message_link = link
        timer.wait_minutes = minutes
        timer.promised = False
        timer.due_at = message.date + timedelta(minutes=minutes)
        timer.notified_at = None
        session.add(timer)
        await session.commit()
    logger.info("Javob kutilmoqda: guruh=%s, %s daqiqa", message.chat.id, minutes)
    return True


async def settle_timer(message: Message, promised_minutes: int | None) -> None:
    """Xodim guruhga yozdi: bu javob hisoblanadi va kutish o'chiriladi. "N daqiqada javob
    beraman" degan bo'lsa kutish shu muddatga suriladi."""
    async with async_session_maker() as session:
        if not promised_minutes:
            result = await session.execute(delete(InquiryTimer).where(InquiryTimer.group_id == message.chat.id))
            await session.commit()
            if result.rowcount:
                logger.info("Xodim javob berdi, kutish yopildi: guruh=%s", message.chat.id)
            return
        timer = (
            await session.execute(select(InquiryTimer).where(InquiryTimer.group_id == message.chat.id))
        ).scalar_one_or_none()
        if timer is None:
            return
        timer.wait_minutes = promised_minutes
        timer.promised = True
        timer.due_at = message.date + timedelta(minutes=promised_minutes)
        timer.notified_at = None
        await session.commit()
    logger.info("Xodim %s daqiqada javob berishni aytdi: guruh=%s", promised_minutes, message.chat.id)


def build_mentions(managers: Sequence[ManagerInfo]) -> str:
    """Menejerlarni belgilaydigan qator: `@username`, username'i yo'q bo'lsa ismiga bosiladigan havola."""
    mentions: list[str] = []
    for manager in managers:
        if manager.username:
            mention = f"@{manager.username}"
        elif manager.user_id:
            name = html.escape(manager.full_name or "Menejer")
            mention = f'<a href="tg://user?id={manager.user_id}">{name}</a>'
        else:
            continue
        if mention not in mentions:
            mentions.append(mention)
    return " ".join(mentions)


def build_hub_alert_text(timer: InquiryTimer, reason: str) -> str:
    group = html.escape(timer.group_title or str(timer.group_id))
    customer = html.escape(timer.customer_name or "mijoz")
    lines = [
        f"⏰ «{group}» guruhida {customer} savol yozganiga {timer.wait_minutes} daqiqa bo'ldi — "
        f"hech kim javob yozmadi.",
        f"<i>{reason}</i>",
    ]
    if timer.message_text:
        text = timer.message_text
        snippet = text if len(text) <= _SNIPPET_LIMIT else text[:_SNIPPET_LIMIT] + "…"
        lines.append(f"\n💬 {html.escape(snippet)}")
    if timer.message_link:
        lines.append(f'🔗 <a href="{timer.message_link}">Xabarga o\'tish</a>')
    return "\n".join(lines)


async def _alert_in_group(bot: Bot, timer: InquiryTimer, managers: Sequence[ManagerInfo]) -> bool:
    mentions = build_mentions(managers)
    if not mentions:
        return False
    try:
        # Alohida xabar: hech qaysi xabarga reply qilinmaydi va iqtibos keltirilmaydi
        await bot.send_message(
            timer.group_id,
            f"{mentions}\n{html.escape(settings.GROUP_ALERT_TEXT)}",
            message_thread_id=timer.thread_id,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return True
    except TelegramAPIError as exc:
        logger.warning("Guruhga ogohlantirish yozib bo'lmadi (group_id=%s): %s", timer.group_id, exc)
        return False


async def send_due_alerts(
    bot: Bot,
    get_managers: Callable[[Bot, int], Awaitable[Sequence[ManagerInfo]]],
    get_hub_thread_id: Callable[[Bot], Awaitable[int | None]],
    now: datetime | None = None,
) -> int:
    """Muddati o'tgan kutishlar bo'yicha ogohlantirish yuboradi. Yuborilganlar sonini qaytaradi."""
    now = now or datetime.now(timezone.utc)
    sent = 0
    async with async_session_maker() as session:
        due = (
            await session.execute(
                select(InquiryTimer).where(InquiryTimer.notified_at.is_(None), InquiryTimer.due_at <= now)
            )
        ).scalars().all()
        for timer in due:
            managers = await get_managers(bot, timer.group_id)
            if await _alert_in_group(bot, timer, managers):
                sent += 1
            else:
                reason = (
                    "Bot bu guruhga yoza olmadi."
                    if build_mentions(managers)
                    else "Bu guruhga menejer biriktirilmagan (/menejer_qosh)."
                )
                try:
                    await bot.send_message(
                        settings.FEEDBACK_HUB_GROUP_ID,
                        build_hub_alert_text(timer, reason),
                        message_thread_id=await get_hub_thread_id(bot),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    sent += 1
                except TelegramAPIError:
                    logger.exception("Ogohlantirishni ichki guruhga ham yuborib bo'lmadi")
            timer.notified_at = now
        await session.commit()
    if due:
        await group_stats.bump(group_stats.STAT_ALERTS, now, amount=len(due))
    return sent
