from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.types import Update, User
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import ceo_state
from app.alerts import start_alerts
from app.config import settings
from app.db.session import init_db
from app.rate_limiter import claim_throttle_notice, is_rate_limited
from app.services.claude_report import REPORTS_DIR
from app.services.memory_digest import start_memory_scheduler
from app.services.reports_cleanup import start_reports_cleanup_scheduler
from app.services.task_reminders import start_task_reminder_scheduler
from app.services.topic_check import verify_topic_projects
from app.telegram.delivery import router as delivery_router
from app.telegram.handlers import router as group_router
from app.telegram.handlers_manager import router as manager_router

# Vaqt belgisi bilan — so'rovlar ketma-ketligi va uzilish qachon boshlanganini logdan ko'rish uchun
_LOG_FORMAT = "%(asctime)s %(levelname)s:%(name)s:%(message)s"
logging.basicConfig(level=settings.LOG_LEVEL, format=_LOG_FORMAT)
log = logging.getLogger(__name__)

LOG_DIR = Path("logs")


def _setup_file_logging() -> None:
    """Loglarni logs/bot.log fayliga ham yozadi (5 MB x 5 fayl aylanadi).

    Ishga tushiruvchi skript har qayta ishga tushirishda run.err.log ni yangidan
    boshlaydi — bot nima uchun to'xtaganini ko'rsatadigan oldingi yozuvlar yo'qolardi."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(LOG_DIR / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logging.getLogger().addHandler(handler)
    except OSError as e:
        log.warning("logs/bot.log ochib bo'lmadi (%s) — loglar faqat konsolga yoziladi", e)


bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
dp.include_router(manager_router)
dp.include_router(delivery_router)
dp.include_router(group_router)

# Bitta xabar uzoq ishlayotganda boshqa xabarlar kutib qolmasligi uchun
# polling update'larni PARALLEL qayta ishlaydi. Har update alohida task sifatida
# bajariladi — biri 30 soniya Claude'da kutsa ham, ikkinchisi darhol boshlanadi.
_ACTIVE_UPDATES: set[asyncio.Task] = set()

# Polling holati — /health shu orqali "jarayon tirik, lekin xabar olmayapti" holatini aniqlaydi
_polling: dict = {"task": None, "last_attempt": 0.0, "last_ok": 0.0}
_POLLING_STALL_SECONDS = 180
# Shundan eski xabar — bot o'chib turganida yig'ilgan navbat (jonli toshqin emas): throttling qo'llanmaydi
_BACKLOG_AGE_SECONDS = 120


def _update_sender(update: Update) -> User | None:
    for attr in ("message", "edited_message", "callback_query", "my_chat_member"):
        obj = getattr(update, attr, None)
        if obj is not None and getattr(obj, "from_user", None) is not None:
            return obj.from_user
    return None


def _update_sender_id(update: Update) -> int | None:
    sender = _update_sender(update)
    return sender.id if sender is not None else None


def _update_message(update: Update):
    """Update'ga tegishli xabar (callback bo'lsa — tugma turgan xabar)."""
    if update.message is not None:
        return update.message
    if update.callback_query is not None:
        return update.callback_query.message
    return None


def _is_backlog(update: Update) -> bool:
    message = update.message
    date = getattr(message, "date", None)
    if date is None:
        return False
    try:
        return time.time() - date.timestamp() > _BACKLOG_AGE_SECONDS
    except Exception:  # noqa: BLE001
        return False


async def _notify_throttled(update: Update, sender: User) -> None:
    """Limit oshganini CEO'ga BIR MARTA aytadi — aks holda uning ortiqcha xabarlari
    (masalan ketma-ket yuborilgan 11-vazifa) hech qanday izsiz yo'qolardi."""
    if not ceo_state.is_ceo(sender.id, sender.username) or not claim_throttle_notice(sender.id):
        return
    message = _update_message(update)
    if message is None:
        return
    try:
        await bot.send_message(
            chat_id=message.chat.id, message_thread_id=getattr(message, "message_thread_id", None),
            text="⚠️ Bir daqiqada juda ko'p xabar keldi — oxirgilari qabul qilinmadi. "
                 "Bir daqiqadan keyin ularni qayta yuboring.",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("throttling haqida xabar yuborilmadi: %s", e)


async def _run_update(update: Update) -> None:
    # P1-5: oddiy throttling — bitta foydalanuvchidan 1 daqiqada 10+ update
    # kelsa, ortiqchasi tashlab yuboriladi (tasodifiy/buzilgan klient
    # toshqinidan himoya — CEO/menejerlarning odatiy ishlatishiga ta'sir qilmaydi).
    sender = _update_sender(update)
    sender_id = sender.id if sender is not None else None
    # CEO (CEO_TELEGRAM_USERNAME ro'yxatidagi har biri) QAYERDA yozmasin — shaxsiy
    # chat, istalgan topik yoki tugma — birinchi update'dayoq raqamli chat_id'si
    # storage/ceo_state.json'ga saqlanadi (proaktiv eslatmalar shunga yuboriladi).
    if sender is not None:
        ceo_state.is_ceo(sender.id, sender.username)
    if sender_id is not None and not _is_backlog(update) and is_rate_limited(sender_id):
        log.warning("update %s: foydalanuvchi %s uchun rate limit — e'tiborsiz qoldirildi",
                    update.update_id, sender_id)
        await _notify_throttled(update, sender)
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
    allowed = dp.resolve_used_update_types()
    # offset=None: Telegram tasdiqlanmagan update'larni (24 soatgacha) saqlab turadi — bot
    # o'chib/qayta ishga tushib turgan paytda kelgan xabarlar ham qayta ishlanadi. Ilgari
    # offset=-1 bilan boshlanardi: bu navbatdagi oxirgisidan boshqa HAMMA xabarni tashlab
    # yuborardi (qayta ishga tushirish paytida yozilgan vazifalar jimgina yo'qolardi).
    offset: int | None = None
    announced = False
    while True:
        _polling["last_attempt"] = time.monotonic()
        try:
            if not announced:
                # Tsikl ichida: ishga tushishda Telegram'ga yetib bo'lmasa (server yonganda tarmoq
                # hali tayyor emas) polling butunlay o'lib qolmaydi — qayta uriniladi
                me = await bot.get_me()
                log.info("Parallel polling started for bot @%s", me.username)
                announced = True
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

        _polling["last_ok"] = time.monotonic()
        if not updates:
            continue
        offset = updates[-1].update_id + 1
        for upd in updates:
            _spawn_update(upd)


def _polling_health() -> tuple[bool, dict]:
    """Polling sog'lommi? Jarayon tirik bo'lib, xabar olmayotgan holatni /health ko'rsatishi
    kerak — aks holda watchdog "hammasi joyida" deb hech qachon qayta ishga tushirmaydi."""
    now = time.monotonic()
    task = _polling["task"]
    info = {
        "polling": "ok",
        "seconds_since_last_poll": round(now - _polling["last_ok"]) if _polling["last_ok"] else None,
    }
    if task is None or task.done():
        info["polling"] = "stopped"
        return False, info
    if now - _polling["last_attempt"] > _POLLING_STALL_SECONDS:
        info["polling"] = "stalled"
        return False, info
    if not _polling["last_ok"] or now - _polling["last_ok"] > _POLLING_STALL_SECONDS:
        # Urinishlar davom etyapti, lekin Telegram'ga yetib bo'lmayapti (tarmoq) —
        # qayta ishga tushirish yordam bermaydi, shuning uchun bu "nosog'lom" emas
        info["polling"] = "telegram unreachable"
    return True, info


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_file_logging()
    await init_db()
    _polling["last_attempt"] = time.monotonic()
    polling_task = asyncio.create_task(_parallel_polling())
    _polling["task"] = polling_task
    memory_tasks = start_memory_scheduler(settings.TELEGRAM_GROUP_ID)
    reports_cleanup_task = start_reports_cleanup_scheduler()
    task_reminder_task = start_task_reminder_scheduler(bot)
    background = [t for t in (start_alerts(bot), asyncio.create_task(verify_topic_projects())) if t]
    log.info("app.started port=%s", settings.APP_PORT)
    yield
    polling_task.cancel()
    reports_cleanup_task.cancel()
    task_reminder_task.cancel()
    for t in [*memory_tasks, *background]:
        t.cancel()
    # Faol update tasklarini kutib olamiz (yopilayotganda qolib ketmasin)
    if _ACTIVE_UPDATES:
        await asyncio.gather(*_ACTIVE_UPDATES, return_exceptions=True)
    await bot.session.close()


# Avtomatik API hujjat sahifalari (/docs, /openapi.json) kerak emas — port tarmoqqa ochiq
app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

REPORTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=REPORTS_DIR), name="reports")


@app.get("/health")
async def health():
    healthy, info = _polling_health()
    return JSONResponse({"status": "ok" if healthy else "unhealthy", **info},
                        status_code=200 if healthy else 503)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.APP_PORT)
