"""
Aiogram 3 Telegram Bot.

Vazifasi: Buyurtmalarni tasdiqlash, rad etish va o'zgartirishlar (feedback) qabul qilish,
til tanlash va asosiy menyu (Balance/Orders/Quotation/Support/Info).

Paket tuzilishi:
    keyboards.py        -- barcha InlineKeyboardMarkup generatorlari
    states.py            -- FSM StatesGroup'lar
    pdf.py               -- buyurtma PDF'ini yuborish
    handlers/start.py       -- /start, til tanlash
    handlers/orders.py      -- tasdiqlash/rad etish/o'zgartirish oqimi
    handlers/menu.py        -- /menu va 5 ta bo'lim tugmasi
    handlers/chat_events.py -- guruhga qo'shilish/a'zolik real-vaqt eventlari
    handlers/group_monitor.py  -- mijoz guruhlarini kuzatish: shikoyat/ijobiy sharh/tovar yo'q ->
                                  ichki guruh topiklari, xodim javobini kutish (ENG BIRINCHI)
    handlers/hub_admin.py      -- ichki guruhdagi buyruqlar: /hisobot, /guruhlar, /menejerlar ...
    handlers/group_mentions.py -- guruhda botni mention qilib yozilgan xabarlar (Odoo bog'lash)
    handlers/fallback.py    -- poll yangilanishi va erkin xabarlar (ENG OXIRIDA ro'yxatdan o'tadi)
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, MenuButtonCommands

from app.bot.keyboards import get_order_inline_keyboard  # fastapi_app.py shundan foydalanadi
from app.config import settings
from app.database import init_db
from app.utils import background_maintenance_loop, odoo_health_check_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("aiogram_bot")

__all__ = ["get_order_inline_keyboard", "main"]


async def set_bot_description(bot: Bot) -> None:
    """Foydalanuvchi botni ochib, /start bosishdan oldin ko'radigan tavsif matnini o'rnatadi."""
    try:
        await bot.set_my_description(
            description=(
                "Assalomu alaykum. «dunyabunya» kompaniyasining rasmiy botiga xush kelibsiz!\n\n"
                "Здравствуйте. Добро пожаловать в официальный бот компании «dunyabunya»!"
            )
        )
    except Exception as exc:
        logger.warning("Bot tavsifini o'rnatishda xatolik: %s", exc)


_MENU_COMMAND = {
    None: [
        BotCommand(command="start", description="Botni ishga tushirish"),
        BotCommand(command="menu", description="Asosiy menyu"),
    ],
    "ru": [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="menu", description="Главное меню"),
    ],
}


async def set_bot_menu_button(bot: Bot) -> None:
    """
    Xabar yozish maydoni yonidagi (chatning ichida emas) doimiy "menyu" belgisini
    "start" va "menu" komandalari bilan yoqadi -- bosilganda ikkita band chiqadi,
    tanlansa bot mos komandani qabul qilib, /start bo'lsa til tanlashni, /menu
    bo'lsa 5 ta bo'limli inline menyuni (handlers/menu.py) ochadi. (Telegram
    bot-chatlarida bu tugma joyi doimiy mavjud -- uni butunlay yashirib bo'lmaydi,
    shu sabab foydali qilib qoldiramiz.)
    """
    try:
        for language_code, commands in _MENU_COMMAND.items():
            await bot.set_my_commands(commands, language_code=language_code)
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as exc:
        logger.warning("Bot menyu tugmasini o'rnatishda xatolik: %s", exc)


async def main() -> None:
    # Tezlik (2026-08-05): handler modullari (jumladan statistics_pdf.py, u matplotlib/
    # fpdf2 ni yuklaydi) ATAYLAB shu yerga (funksiya ichiga, modul darajasiga emas)
    # ko'chirildi. Sabab: app/bot/keyboards.py ni fastapi_app.py import qilganda ham
    # Python avval shu paketning __init__.py sini bajarishi SHART -- agar bu importlar
    # modul darajasida bo'lganida, fastapi_app.py hech qachon chaqirmaydigan
    # matplotlib/fpdf2/barcha handlerlar baribir 3 ta uvicorn worker jarayoniga ham
    # yuklanib qolardi (aynan shu muammo tekshirilib topildi). main() esa FAQAT
    # bot konteynerida (python -m app.bot) chaqiriladi, shu sabab bu yerga ko'chirish
    # xulq-atvorni o'zgartirmaydi -- faqat qachon import qilinishini kechiktiradi.
    from app import daily_report
    from app.bot.handlers import (
        chat_events, edit_info, fallback, group_mentions, group_monitor, hub_admin, menu, orders, start,
    )

    await init_db()
    bot_instance = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Ro'yxatdan o'tish tartibi muhim: fallback.router ENG OXIRIDA bo'lishi kerak,
    # chunki u StateFilter(None) bilan boshqa hech kim ushlamagan istalgan xabarni oladi.
    # group_monitor ENG BIRINCHI: guruhdagi har bir xabarni ko'rib, SkipHandler bilan
    # keyingilarga o'tkazib yuboradi (handlers/group_monitor.py).
    dp.include_router(group_monitor.router)
    dp.include_router(hub_admin.router)
    dp.include_router(start.router)
    dp.include_router(orders.router)
    dp.include_router(menu.router)
    dp.include_router(edit_info.router)
    dp.include_router(chat_events.router)
    dp.include_router(group_mentions.router)
    dp.include_router(fallback.router)

    await set_bot_description(bot_instance)
    await set_bot_menu_button(bot_instance)

    # 2026-08-05, PRODUCTION tayyorlash/tezlashtirish: davriy DB tozalash vazifasi
    # (buyurtma expiry, eski yozuv/fayl/webhook-log tozalash) avval `fastapi_app.py`
    # ichida edi -- FastAPI endi bir nechta uvicorn WORKER bilan ishlagani sabab
    # (har biri o'z alohida jarayoni) u yerda qolsa HAR bir worker buni mustaqil/
    # parallel ishga tushirar edi. Bu yerga (aiogram polling -- me'moriy jihatdan
    # HAR DOIM yagona nusxada ishlaydigan yagona jarayon) ko'chirildi, shu sabab
    # aniq FAQAT BIR MARTA ishlaydi -- `app/utils.py`dagi funksiya izohiga qarang.
    asyncio.create_task(background_maintenance_loop())
    # Odoo API kaliti ~90 kunda tugaydi -- "unutib qo'yish"ga qarshi davriy tekshiruv
    # (2026-08-06), ishlamay qolsa ODOO_ALERT_CHAT_ID'ga xabar beradi. `app/utils.py`dagi
    # `odoo_health_check_loop` docstringiga qarang.
    asyncio.create_task(odoo_health_check_loop(bot_instance))

    # Mijoz xodimga murojaat qilgach javobsiz qolgan holatlar bo'yicha eslatmalar (2026-10-07)
    asyncio.create_task(group_monitor.staff_reply_reminder_loop(bot_instance))
    # Har yarim tunda o'tgan kunning statistika hisoboti (2026-10-12, `app/daily_report.py`)
    asyncio.create_task(daily_report.daily_report_loop(bot_instance))

    logger.info("Aiogram Bot ishga tushdi.")
    await dp.start_polling(bot_instance)


if __name__ == "__main__":
    asyncio.run(main())
