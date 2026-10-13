"""Fon nazorati: muammoni CEO sezishidan oldin dasturchiga bildirish.

Har daqiqada hisoblagichlarni diskka yozadi; har 5 daqiqada Odoo va ma'lumotlar bazasini
tekshiradi va holat O'ZGARGANDA (uzildi / tiklandi) bir marta xabar beradi — uzilish
davomida har 5 daqiqada takrorlab bezovta qilmaydi. Kuniga bir marta zaxira nusxa
yangiligi va diskdagi bo'sh joyni tekshiradi, eskirgan suhbat tarixini o'chiradi (P3-4)
va dushanba kuni haftalik salomatlik hisobotini yuboradi (P3-6).

Xabarlar ERROR log orqali ketadi: ALERT_CHAT_ID sozlangan bo'lsa Telegram'ga, aks holda
faqat logs/bot.log ga.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app import alerts, metrics, scheduler_state
from app.config import settings
from app.db import repo
from app.db.session import SessionLocal
from app.services import status as status_service

log = logging.getLogger(__name__)

_TZ = ZoneInfo("Asia/Tashkent")
_TICK_SECONDS = 60
_PROBE_EVERY_TICKS = 5
_DAILY_HOUR = 9          # kunlik tekshiruvlar shu soatdan keyin bir marta
_WEEKLY_WEEKDAY = 0      # dushanba


class _Watch:
    """Bitta bog'liqlik (Odoo, baza) holatini kuzatadi va faqat o'zgarishni xabar qiladi."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.down_since: float | None = None

    def update(self, problem: str | None, hint: str = "") -> None:
        if problem and self.down_since is None:
            self.down_since = time.monotonic()
            log.error("monitor: %s ishlamayapti — %s%s", self.name, problem, f" | {hint}" if hint else "")
        elif not problem and self.down_since is not None:
            minutes = max(1, round((time.monotonic() - self.down_since) / 60))
            self.down_since = None
            log.info("monitor: %s tiklandi (%d daqiqa ishlamadi)", self.name, minutes)
            alerts.notify(f"✅ {self.name} tiklandi ({minutes} daqiqa ishlamadi)")


_odoo_watch = _Watch("Odoo bilan aloqa")
_db_watch = _Watch("Ma'lumotlar bazasi")


async def probe() -> None:
    """Odoo va bazani jonli tekshiradi (holat o'zgarsa — xabar)."""
    odoo = await status_service._odoo.diagnose()
    _odoo_watch.update(None if odoo.get("aloqa") else str(odoo.get("sabab") or "aloqa yo'q"),
                       str(odoo.get("tavsiya") or ""))
    _db_watch.update(await status_service.check_database())


async def purge_old_conversations() -> int:
    """P3-4: saqlash muddatidan eski suhbat tarixini o'chiradi (0 = o'chirilmaydi)."""
    days = settings.CONVERSATION_RETENTION_DAYS
    if days <= 0:
        return 0
    async with SessionLocal() as session:
        removed = await repo.delete_old_conversation_messages(session, days)
    if removed:
        log.info("monitor: %d ta %d kundan eski suhbat xabari o'chirildi", removed, days)
    return removed


async def daily_checks() -> None:
    backup = status_service.latest_backup()
    if backup is None:
        log.error("monitor: zaxira nusxa topilmadi (%s) — dunyabunya_db_backup vazifasini tekshiring",
                  settings.BACKUP_DIR)
    elif backup[1] > status_service.BACKUP_MAX_AGE_HOURS:
        log.error("monitor: oxirgi zaxira nusxa %d soat oldin olingan — dunyabunya_db_backup "
                  "vazifasi ishlamayapti", round(backup[1]))
    current = await status_service.collect_status()
    if current["free_gb"] is not None and current["free_gb"] < status_service.DISK_MIN_FREE_GB:
        log.error("monitor: diskda bo'sh joy kam — %.1f GB", current["free_gb"])
    try:
        await purge_old_conversations()
    except Exception:
        log.exception("monitor: eski suhbat tarixini o'chirishda xato")


async def weekly_report(bot) -> None:
    """P3-6: haftalik "tizim salomatligi" hisoboti — ogohlantirish chatiga."""
    if not settings.ALERT_CHAT_ID:
        return
    current = await status_service.collect_status()
    text = status_service.format_status(current, title="Haftalik tizim hisoboti")
    await bot.send_message(chat_id=settings.ALERT_CHAT_ID, text=text, parse_mode="HTML")


async def _run_scheduled(bot) -> None:
    now = datetime.now(_TZ)
    today = now.date().isoformat()
    if now.hour >= _DAILY_HOUR and scheduler_state.last_run("monitor_daily") != today:
        scheduler_state.mark_run("monitor_daily", today)
        await daily_checks()
    if (now.weekday() == _WEEKLY_WEEKDAY and now.hour >= _DAILY_HOUR
            and scheduler_state.last_run("weekly_report") != today):
        scheduler_state.mark_run("weekly_report", today)
        await weekly_report(bot)


async def _loop(bot) -> None:
    log.info("monitor: ishga tushdi (Odoo/baza har %d daqiqada tekshiriladi)", _PROBE_EVERY_TICKS)
    tick = 0
    while True:
        await asyncio.sleep(_TICK_SECONDS)
        tick += 1
        try:
            await asyncio.to_thread(metrics.flush)
            if tick % _PROBE_EVERY_TICKS == 0:
                await probe()
                await _run_scheduled(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("monitor: tekshiruvda xato")


def start_monitor(bot) -> asyncio.Task:
    """main.py lifespan ichida chaqiriladi."""
    logging.getLogger().addHandler(metrics.ErrorCounter())
    return asyncio.create_task(_loop(bot))
