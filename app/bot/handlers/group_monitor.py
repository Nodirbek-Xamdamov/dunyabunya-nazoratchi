"""
Mijoz guruhlaridagi xabarlarni yig'ish (2026-10-13).

Bot mijozlar bor guruhlarda FAQAT O'QIYDI -- hech narsa yozmaydi, reaksiya qo'ymaydi, hech
qayerga uzatmaydi. Har bir xabar (matn, media izohi, tahrir) darhol monitoring bazasiga yoziladi
(`app/monitor_db.py`) va shu bilan handlerning ishi tugaydi: tasniflash alohida fon vazifada
(`app/monitor_classifier.py`), shuning uchun xabar qabul qilish modelni kutib to'xtab qolmaydi
va bot qayta ishga tushsa ham hech narsa yo'qolmaydi.

Saqlanmaydi: xizmat xabarlari (qo'shildi/chiqdi va h.k.), botlarning xabarlari, bog'langan
kanaldan avtomatik tushgan postlar, hisobot guruhining o'zi (`REPORT_CHAT_ID`) va
`GROUP_MONITOR_EXCLUDED_GROUP_IDS`dagi guruhlar.

Xodim (`is_staff`) = guruh admini yoki egasi (anonim admin ham). Xodim xabarlari tasniflanmaydi,
lekin suhbat konteksti va "5 daqiqa qoidasi" uchun saqlanadi.

Bot guruhdagi oddiy xabarlarni ko'rishi uchun shu guruhda ADMIN bo'lishi (yoki BotFather'da
privacy mode o'chirilgan bo'lishi) shart.

Bu router ENG BIRINCHI ro'yxatdan o'tadi va oxirida `SkipHandler` tashlaydi, shu sabab keyingi
handlerlar (masalan `group_mentions`) o'sha xabarni odatdagidek qayta ishlaydi.
"""

import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message

from app import monitor_classifier, monitor_db, monitor_sla
from app.config import settings

logger = logging.getLogger("aiogram_bot")

router = Router(name="group_monitor")

_GROUP_CHAT_TYPES = {"group", "supergroup"}

_SERVICE_CONTENT_TYPES = {
    "new_chat_members", "left_chat_member", "new_chat_title", "new_chat_photo", "delete_chat_photo",
    "group_chat_created", "supergroup_chat_created", "pinned_message", "migrate_to_chat_id",
    "migrate_from_chat_id", "forum_topic_created", "forum_topic_edited", "forum_topic_closed",
    "forum_topic_reopened", "general_forum_topic_hidden", "general_forum_topic_unhidden",
    "video_chat_started", "video_chat_ended", "video_chat_scheduled", "video_chat_participants_invited",
    "message_auto_delete_timer_changed", "boost_added",
}
# Fayl ID'si saqlanadigan media turlari (tekshirish tartibida: gif ham "document" bo'lib keladi)
_MEDIA_TYPES = ("voice", "video_note", "video", "audio", "animation", "sticker", "document")


def excluded_chat_ids() -> set[int]:
    ids = {int(part) for part in settings.GROUP_MONITOR_EXCLUDED_GROUP_IDS.replace(" ", "").split(",") if part}
    if settings.REPORT_CHAT_ID:
        ids.add(settings.REPORT_CHAT_ID)
    return ids


def media_of(message: Message) -> tuple[str, str | None]:
    """Xabarning media turi va (bo'lsa) Telegram fayl ID'si."""
    for media_type in _MEDIA_TYPES:
        media = getattr(message, media_type, None)
        if media is not None:
            return media_type, getattr(media, "file_id", None)
    photo = getattr(message, "photo", None)
    if photo:
        return "photo", photo[-1].file_id
    return ("text", None) if message.text else ("other", None)


def sender_of(message: Message) -> tuple[int, str | None, str | None, bool] | None:
    """(ID, username, ism, anonim adminmi). None -- bu xabar saqlanmaydi (bot yoki kanal posti)."""
    sender_chat = getattr(message, "sender_chat", None)
    if sender_chat is not None:
        if sender_chat.id == message.chat.id:
            # Anonim admin: guruh nomidan yozadi
            return sender_chat.id, None, getattr(message, "author_signature", None) or "Admin", True
        return None  # bog'langan kanal posti yoki boshqa kanal nomidan yozilgan xabar
    user = message.from_user
    if user is None or user.is_bot:
        return None
    return user.id, user.username, user.full_name, False


def _edit_time(message: Message) -> datetime | None:
    """Tahrir vaqti (aiogram uni Unix soniyalarida beradi)."""
    edited = getattr(message, "edit_date", None)
    if isinstance(edited, (int, float)):
        return datetime.fromtimestamp(edited, tz=timezone.utc)
    return edited


async def collect(message: Message, *, edited: bool = False) -> None:
    """Guruh xabarini (yoki uning tahririni) bazaga yozadi. Tahrir faqat bazada bor xabarni
    yangilaydi: bot ko'rmagan eski xabarning tahriri yangi xabar sifatida yozilmaydi (aks holda
    u o'tib ketgan kunlarning hisobiga qo'shilib qolardi)."""
    if not settings.MONITOR_ENABLED or message.chat.id in excluded_chat_ids():
        return
    if message.content_type in _SERVICE_CONTENT_TYPES:
        return
    sender = sender_of(message)
    if sender is None:
        return
    sender_id, username, name, anonymous_admin = sender

    group = await monitor_sla.group_for_chat(message.bot, message.chat.id, message.chat.title)
    is_staff = anonymous_admin or sender_id in group.admin_ids
    media_type, file_id = media_of(message)
    replied = message.reply_to_message
    # Forum-guruhda topik ichidagi har bir xabarga topikni yaratgan servis xabari "reply" bo'lib keladi
    reply_to = replied.message_id if replied is not None and not replied.forum_topic_created else None

    is_new = await monitor_db.save_message(
        chat_id=message.chat.id,
        message_id=message.message_id,
        sender_id=sender_id,
        sender_username=username,
        sender_name=name,
        is_staff=is_staff,
        text=message.text or message.caption,
        media_type=media_type,
        file_id=file_id,
        reply_to_message_id=reply_to,
        sent_at=message.date,
        edited_at=_edit_time(message),
        only_update=edited,
        # Ovoz/video davomiyligi -- juda uzun yozuv matnga aylantirilmaydi
        media_duration=getattr(getattr(message, media_type, None), "duration", None),
    )
    if is_new and not is_staff:
        monitor_classifier.wake()


@router.message(F.chat.type.in_(_GROUP_CHAT_TYPES))
async def handle_group_message(message: Message) -> None:
    try:
        await collect(message)
    except Exception:
        logger.exception("Guruh xabarini saqlashda xatolik (chat_id=%s)", message.chat.id)
    # Xabar keyingi handlerlarga (group_mentions va h.k.) ham yetib borishi kerak
    raise SkipHandler


@router.edited_message(F.chat.type.in_(_GROUP_CHAT_TYPES))
async def handle_group_edit(message: Message) -> None:
    try:
        await collect(message, edited=True)
    except Exception:
        logger.exception("Tahrirlangan xabarni saqlashda xatolik (chat_id=%s)", message.chat.id)
