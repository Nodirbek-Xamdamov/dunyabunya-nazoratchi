"""
Monitoring uchun admin buyrug'i (2026-10-13).

    /test_report [bugun | kecha | YYYY-MM-DD]

Hisobotni darhol tuzib, hisobot guruhiga yuboradi (kunni "yuborilgan" deb belgilamaydi --
yarim tundagi hisobot baribir ketadi). Sana berilmasa -- bugungi kun (hozirgacha).

Faqat adminlar uchun: `ADMIN_USER_IDS`dagi ID'lar, `ADMIN_CHAT_ID` egasi va hisobot guruhining
adminlari. Buyruq faqat shaxsiy chatda yoki hisobot guruhida ishlaydi; boshqa joyda va boshqa
odamlarga bot hech narsa demaydi (mijoz guruhlarida bot yozmaydi).
"""

import logging
from datetime import date, timedelta

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app import monitor_db, monitor_report
from app.config import settings

logger = logging.getLogger("aiogram_bot")

router = Router(name="monitor_admin")


def _configured_admins() -> set[int]:
    ids = {int(part) for part in settings.ADMIN_USER_IDS.replace(" ", "").split(",") if part.lstrip("-").isdigit()}
    if monitor_report.admin_chat_id():
        ids.add(monitor_report.admin_chat_id())
    return ids


async def is_admin(bot: Bot, user_id: int) -> bool:
    if user_id in _configured_admins():
        return True
    if not settings.REPORT_CHAT_ID:
        return False
    try:
        member = await bot.get_chat_member(settings.REPORT_CHAT_ID, user_id)
    except TelegramAPIError:
        return False
    return member.status in ("creator", "administrator")


def parse_day(argument: str | None) -> date | None:
    """Buyruq argumenti -> hisobot kuni. Bo'sh yoki "bugun" -- hozirgi hisobot davri (oxirgi
    `REPORT_TIME`dan hozirgacha), "kecha" -- undan oldingisi. Xato bo'lsa None."""
    argument = (argument or "").strip().lower()
    today = monitor_db.report_day()
    if argument in ("", "bugun"):
        return today
    if argument == "kecha":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(argument)
    except ValueError:
        return None


@router.message(Command("test_report"))
async def handle_test_report(message: Message, command: CommandObject) -> None:
    if message.chat.type != "private" and message.chat.id != settings.REPORT_CHAT_ID:
        return
    if message.from_user is None or not await is_admin(message.bot, message.from_user.id):
        return
    day = parse_day(command.args)
    if day is None:
        await message.reply("Sana noto'g'ri. Masalan: /test_report, /test_report kecha, /test_report 2026-10-13")
        return
    await message.reply(f"⏳ {day.isoformat()} uchun hisobot tayyorlanmoqda…")
    try:
        sent = await monitor_report.run_report(message.bot, day, mark_sent=False)
    except Exception:
        logger.exception("/test_report: hisobotni tuzishda xatolik")
        await message.reply("❌ Hisobotni tuzishda xatolik bo'ldi (logga yozildi).")
        return
    if not sent:
        await message.reply("❌ Hisobot tuzildi, lekin guruhga yuborib bo'lmadi (logga qarang).")
    elif message.chat.id != settings.REPORT_CHAT_ID:
        await message.reply("✅ Hisobot hisobot guruhiga yuborildi.")
