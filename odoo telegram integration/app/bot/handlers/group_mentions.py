"""
Guruhda botni (@BOT_USERNAME) mention qilib yozilgan xabarlar (2026-08-03).

Bot shu guruhda ADMIN bo'lishi shart -- aks holda Telegram'ning privacy mode cheklovi
tufayli bot faqat komanda/reply/mention xabarlarinigina emas, oddiy xabarlarni ham
umuman ko'rmaydi (admin bo'lgan botlar privacy mode'dan qat'i nazar barcha xabarlarni
oladi). Guruh a'zosi botni mention qilib biror narsa yozganda, xabar matni FastAPI orqali
HAQIQIY crm.lead sifatida Odoo'ga yoziladi (2026-08-03, ikkinchi pivot -- avval faqat
Odoo'da TOPILGAN yuboruvchilar uchun passiv GroupInquiry yozuvi saqlanardi, endi
TOPILMAGAN yuboruvchilar ham "yangi mijoz" sifatida (partner_id yo'q, contact_name =
full_name/username) crm.lead oladi -- obyekt tanlash/lokatsiya so'ralmaydi, call-center
Odoo'dagi lead orqali o'zi bog'lanadi):
  - yuboruvchi Odoo'da (x_telegram_id bo'yicha) TOPILSA -- lead partner_id bilan yoziladi;
  - TOPILMASA -- lead contact_name bilan (partner_id yo'q) yoziladi.

2026-10-07: bot mijoz guruhlariga HECH NARSA YOZMAYDI (faqat o'qiydi -- `group_monitor.py`),
shu sabab avvalgi "so'rovingiz qabul qilindi" / xatolik javoblari olib tashlandi. Lead
yaratish o'zgarmagan.

Bot o'zi guruh xabar hodisasidan yuboruvchining chat_id/username/ismini to'g'ridan-to'g'ri
oladi -- buning uchun alohida premium (Telethon) akkaunt shart emas.
"""

import logging

from aiogram import F, Router
from aiogram.types import Message

from app.bot.internal_api import get_internal_client
from app.config import settings

logger = logging.getLogger("aiogram_bot")

router = Router(name="group_mentions")

_GROUP_CHAT_TYPES = {"group", "supergroup"}


async def submit_group_inquiry(
    chat_id: int,
    username: str | None,
    full_name: str | None,
    group_id: int,
    group_title: str | None,
    message_text: str,
) -> dict | None:
    """FastAPI'ga POST qiladi -- {"found": bool, "lead_created": bool} qaytaradi (yuboruvchi
    Odoo'da topilgan-topilmaganidan qat'i nazar, crm.lead yaratishga urinadi). Tarmoq/server
    xatosida None qaytadi (bu holda chaqiruvchi hech qanday javob bermasligi kerak --
    vaqtinchalik xatoni muvaffaqiyatsizlik deb noto'g'ri talqin qilmaslik uchun)."""
    payload = {
        "chat_id": chat_id,
        "username": username,
        "full_name": full_name,
        "group_id": group_id,
        "group_title": group_title,
        "message_text": message_text,
    }
    try:
        client = get_internal_client()
        resp = await client.post(
            "/api/v1/bot/group-inquiry",
            json=payload,
            headers={"X-API-Key": settings.API_KEY},
        )
        if resp.status_code == 200:
            data = resp.json()
            return {"found": data.get("found", False), "lead_created": data.get("lead_created", False)}
    except Exception:
        logger.exception("Guruh mention xabarini FastAPI'ga yuborishda xatolik")
    return None


@router.message(F.chat.type.in_(_GROUP_CHAT_TYPES), F.text)
async def handle_group_mention(message: Message) -> None:
    bot_username = settings.BOT_USERNAME.lstrip("@").lower()
    if f"@{bot_username}" not in message.text.lower():
        return

    sender = message.from_user
    if sender is None or sender.is_bot:
        return

    await submit_group_inquiry(
        chat_id=sender.id,
        username=sender.username,
        full_name=sender.full_name,
        group_id=message.chat.id,
        group_title=message.chat.title,
        message_text=message.text,
    )
