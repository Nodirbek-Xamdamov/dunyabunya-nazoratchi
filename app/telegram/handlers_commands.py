"""Umumiy buyruqlar: /yordam, /id, /status, /brifing.

Bu router dispatcher'da BIRINCHI turadi — aks holda guruhdagi "/brifing" kabi buyruq oddiy
xabar sifatida tahlilga (vazifami-yo'qmi) tushib ketardi. Guruhda bot faqat CEO'larga javob
beradi, shaxsiy chatda esa har kim o'z roliga mos yordam matnini oladi.
"""

from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat, Message

from app import ceo_state
from app.db import repo
from app.db.session import SessionLocal
from app.services import briefing, status as status_service
from app.services.errors import friendly_error
from app.telegram import replies

log = logging.getLogger(__name__)

router = Router()

HELP_CEO = """<b>dunyabunya yordamchisi</b>

Oddiy gap bilan yozing yoki ovozli xabar yuboring — shu chatda yoki guruhning General mavzusida.

<b>Savol va tahlil</b>
• Shu oydagi sotuv qancha, o'tgan oyga nisbatan-chi?
• Eng katta 5 ta qarzdor kim?
• Sotuv nega tushdi — qaysi mijozlar hisobiga?
• Rotband qoldig'i qaysi omborlarda?
• Bugun dollar kursi qancha?

<b>Hisobot va fayllar</b>
• Xodimlar ro'yxatini Excelda ber
• Kompaniya statistikasi — grafikli hisobot
• Rasm, PDF, Excel yoki Word fayl yuboring — o'qib, kerakli ma'lumotni ajratib beraman

<b>Topshiriq va eslatma</b>
• Sardorga vazifa ber: ertagacha ombor qoldig'ini tekshirsin
• 123-vazifaning muddatini jumaga sur
• Ertaga 9 da eslat: bank bilan uchrashuv
• Har kuni 18:00 da bugungi sotuvni yuborib tur
• Eslab qol: summalarni doim dollarda ham ko'rsat

<b>Boshqa ishlar</b>
Xat va e'lon yozish, tarjima, hisob-kitob, maslahat.

Guruhdagi bo'lim mavzusiga yozilgan topshiriq avvalgidek Odoo'ga vazifa bo'lib yoziladi; noto'g'ri yozilgan bo'lsa — xabar ostidagi «O'chirish» tugmasi.

<b>Buyruqlar</b>
/brifing — bugungi qisqa xulosa
/status — tizim holati
/otask &lt;raqam&gt; — vazifani o'chirish
/menejer_biriktir, /menejer_ochir — menejerga ruxsat berish / olish
/id — chat raqamini ko'rish"""

HELP_MANAGER = """<b>dunyabunya yordamchisi</b>

Shu chatga o'z bo'limingiz bo'yicha oddiy gap bilan yozing yoki ovozli xabar yuboring: ro'yxat, qoldiq, yig'indi, solishtirish yoki hisobot so'rang. Uzun ro'yxat uchun Excel / HTML / Matn tugmalari chiqadi. Rasm, PDF, Excel yoki Word fayl yuborsangiz — o'qib beraman.

Eslatma ham qo'ya olasiz: «ertaga 10 da eslat: inventarizatsiya».

<b>Buyruqlar</b>
/otask &lt;raqam&gt; — o'zingiz yaratgan vazifani o'chirish
/id — chat raqamini ko'rish"""

HELP_EMPLOYEE = """<b>dunyabunya boti</b>

Sizga topshirilgan vazifalar shu chatga keladi. Vazifani bajargach, xabar ostidagi «Bajarildi» tugmasini bosing.

<b>Buyruqlar</b>
/start — ro'yxatdan o'tish
/unlink — bog'lanishni bekor qilish
/id — chat raqamini ko'rish"""


def _is_private(message: Message) -> bool:
    return message.chat.type == "private"


async def _require_ceo(message: Message) -> bool:
    """CEO bo'lmasa: shaxsiy chatda sababini aytadi, guruhda jim qoladi."""
    user = message.from_user
    if user is not None and ceo_state.is_ceo(user.id, user.username):
        return True
    if _is_private(message):
        await message.answer(replies.not_ceo_text())
    return False


@router.message(Command("yordam", "help"))
async def on_help(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    if ceo_state.is_ceo(user.id, user.username):
        text = HELP_CEO
    elif not _is_private(message):
        return  # guruhda bot faqat CEO'ga javob beradi
    else:
        async with SessionLocal() as session:
            manager = await repo.get_manager_by_telegram_id(session, user.id)
        text = HELP_MANAGER if manager is not None else HELP_EMPLOYEE
    await message.answer(text, parse_mode="HTML")


@router.message(Command("id"))
async def on_id(message: Message) -> None:
    """Chat raqamlari — masalan .env dagi ALERT_CHAT_ID ni to'ldirish uchun."""
    user = message.from_user
    if user is None:
        return
    if not _is_private(message) and not ceo_state.is_ceo(user.id, user.username):
        return
    lines = [f"Sizning Telegram ID: <code>{user.id}</code>", f"Shu chat ID: <code>{message.chat.id}</code>"]
    if message.message_thread_id:
        lines.append(f"Mavzu (topik) ID: <code>{message.message_thread_id}</code>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("status", "holat"))
async def on_status(message: Message) -> None:
    if not await _require_ceo(message):
        return
    try:
        current = await status_service.collect_status()
    except Exception as e:
        log.exception("/status: holatni yig'ishda xato")
        await message.answer(f"⚠️ Holatni tekshirib bo'lmadi: {friendly_error(e)}")
        return
    await message.answer(status_service.format_status(current), parse_mode="HTML")


@router.message(Command("brifing", "xulosa"))
async def on_briefing(message: Message, bot: Bot) -> None:
    if not await _require_ceo(message):
        return
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing",
                                   message_thread_id=message.message_thread_id)
        text = briefing.format_briefing(await briefing.collect_briefing())
    except Exception as e:
        log.exception("/brifing: xulosani tayyorlashda xato")
        await message.answer(f"⚠️ Xulosani tayyorlab bo'lmadi: {friendly_error(e)}")
        return
    await message.answer(text, parse_mode="HTML")


_COMMON_COMMANDS = [
    BotCommand(command="yordam", description="Bot nima qila oladi"),
    BotCommand(command="start", description="Ro'yxatdan o'tish"),
    BotCommand(command="id", description="Chat raqamini ko'rish"),
]
_CEO_COMMANDS = [
    BotCommand(command="brifing", description="Bugungi qisqa xulosa"),
    BotCommand(command="status", description="Tizim holati"),
    BotCommand(command="yordam", description="Bot nima qila oladi"),
    BotCommand(command="id", description="Chat raqamini ko'rish"),
]


async def publish_commands(bot: Bot) -> None:
    """Telegram'dagi buyruqlar menyusini ("/" bosilganda chiqadigan ro'yxat) o'rnatadi:
    hamma uchun umumiy, ma'lum CEO'lar uchun — kengaytirilgan. Xato bo'lsa bot ishlayveradi."""
    try:
        await bot.set_my_commands(_COMMON_COMMANDS, scope=BotCommandScopeAllPrivateChats())
        for ceo_id in await ceo_state.get_ceo_telegram_user_ids():
            await bot.set_my_commands(_CEO_COMMANDS, scope=BotCommandScopeChat(chat_id=ceo_id))
    except Exception as e:  # noqa: BLE001
        log.warning("buyruqlar menyusini o'rnatib bo'lmadi: %s", e)
