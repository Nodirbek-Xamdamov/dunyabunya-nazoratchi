from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.types import Update, User
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import ceo_state
from app.config import settings
from app.db.session import init_db
from app.rate_limiter import is_rate_limited
from app.services.claude_report import REPORTS_DIR
from app.services.memory_digest import start_memory_scheduler
from app.services.reports_cleanup import start_reports_cleanup_scheduler
from app.services.task_reminders import start_task_reminder_scheduler
from app.telegram.delivery import router as delivery_router
from app.telegram.handlers import router as group_router
from app.telegram.handlers_manager import router as manager_router

logging.basicConfig(level=settings.LOG_LEVEL)
log = logging.getLogger(__name__)

bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
dp.include_router(manager_router)
dp.include_router(delivery_router)
dp.include_router(group_router)

# Bitta xabar uzoq ishlayotganda boshqa xabarlar kutib qolmasligi uchun
# polling update'larni PARALLEL qayta ishlaydi. Har update alohida task sifatida
# bajariladi — biri 30 soniya Claude'da kutsa ham, ikkinchisi darhol boshlanadi.
_ACTIVE_UPDATES: set[asyncio.Task] = set()


def _update_sender(update: Update) -> User | None:
    for attr in ("message", "edited_message", "callback_query", "my_chat_member"):
        obj = getattr(update, attr, None)
        if obj is not None and getattr(obj, "from_user", None) is not None:
            return obj.from_user
    return None


def _update_sender_id(update: Update) -> int | None:
    sender = _update_sender(update)
    return sender.id if sender is not None else None


async def _run_update(update: Update) -> None:
    # P1-5: oddiy throttling — bitta foydalanuvchidan 1 daqiqada 10+ update
    # kelsa, ortiqchasi jimgina tashlab yuboriladi (tasodifiy/buzilgan klient
    # toshqinidan himoya — CEO/menejerlarning odatiy ishlatishiga ta'sir qilmaydi).
    sender = _update_sender(update)
    sender_id = sender.id if sender is not None else None
    # CEO (CEO_TELEGRAM_USERNAME ro'yxatidagi har biri) QAYERDA yozmasin — shaxsiy
    # chat, istalgan topik yoki tugma — birinchi update'dayoq raqamli chat_id'si
    # storage/ceo_state.json'ga saqlanadi (proaktiv eslatmalar shunga yuboriladi).
    if sender is not None:
        ceo_state.is_ceo(sender.id, sender.username)
    if sender_id is not None and is_rate_limited(sender_id):
        log.warning("update %s: foydalanuvchi %s uchun rate limit — e'tiborsiz qoldirildi",
                    update.update_id, sender_id)
        return
    try:
        await dp.feed_update(bot, update)
    except Exception as e:  # noqa: BLE001
        log.exception("update %s ishlov berishda xato: %s", update.update_id, e)


def _spawn_update(update: Update) -> None:
    task = asyncio.create_task(_run_update(update))
    _ACTIVE_UPDATES.add(task)
    task.add_done_callback(_ACTIVE_UPDATES.discard)


async def _parallel_polling() -> None:
    """aiogram start_polling o'rniga parallel polling: har update'ni alohida task qiladi."""
    me = await bot.get_me()
    log.info("Parallel polling started for bot @%s", me.username)
    allowed = dp.resolve_used_update_types()
    offset = -1
    while True:
        try:
            updates = await bot.get_updates(
                offset=offset,
                timeout=30,
                allowed_updates=allowed,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("get_updates xato: %s — 3s keyin qayta uriniladi", e)
            await asyncio.sleep(3)
            continue

        if not updates:
            continue
        offset = updates[-1].update_id + 1
        for upd in updates:
            _spawn_update(upd)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    polling_task = asyncio.create_task(_parallel_polling())
    memory_tasks = start_memory_scheduler(settings.TELEGRAM_GROUP_ID)
    reports_cleanup_task = start_reports_cleanup_scheduler()
    task_reminder_task = start_task_reminder_scheduler(bot)
    log.info("app.started port=%s", settings.APP_PORT)
    yield
    polling_task.cancel()
    reports_cleanup_task.cancel()
    task_reminder_task.cancel()
    for t in memory_tasks:
        t.cancel()
    # Faol update tasklarini kutib olamiz (yopilayotganda qolib ketmasin)
    if _ACTIVE_UPDATES:
        await asyncio.gather(*_ACTIVE_UPDATES, return_exceptions=True)
    await bot.session.close()


app = FastAPI(lifespan=lifespan)

REPORTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=REPORTS_DIR), name="reports")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.APP_PORT)