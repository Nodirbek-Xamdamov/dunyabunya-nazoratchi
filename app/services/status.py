"""Tizim holati: /status buyrug'i, haftalik salomatlik hisoboti (P3-6) va monitoring uchun.

`collect_status()` jonli tekshiradi (Telegram polling, Odoo, ma'lumotlar bazasi, zaxira
nusxa, disk) va hisoblagichlarni qo'shadi; `format_status()` uni Telegram xabariga aylantiradi.
"""

from __future__ import annotations

import html
import shutil
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app import metrics, runtime
from app.config import settings
from app.db.session import SessionLocal
from app.services.odoo import OdooClient

_TZ = ZoneInfo("Asia/Tashkent")
_odoo = OdooClient()

BACKUP_MAX_AGE_HOURS = 36      # kunlik zaxira nusxa shundan eski bo'lsa — muammo
DISK_MIN_FREE_GB = 2.0


def is_test_odoo() -> bool:
    """Odoo.sh'ning sinov/ishlab chiqish bazasi (*.dev.odoo.com). U qayta qurilganda manzili
    almashadi va ma'lumoti prod nusxasiga qaytadi — productionda ishlatib bo'lmaydi."""
    return _odoo.host.endswith(".dev.odoo.com")


def _duration(seconds: int) -> str:
    days, rest = divmod(max(0, int(seconds)), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = [f"{days} kun"] if days else []
    if hours:
        parts.append(f"{hours} soat")
    if not days:
        parts.append(f"{minutes} daqiqa")
    return " ".join(parts)


def latest_backup() -> tuple[Path, float] | None:
    """Eng yangi zaxira nusxa fayli va uning yoshi (soat). Papka/fayl yo'q bo'lsa None."""
    try:
        files = sorted(Path(settings.BACKUP_DIR).glob("*.dump"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    if not files:
        return None
    return files[-1], (time.time() - files[-1].stat().st_mtime) / 3600


async def check_database() -> str | None:
    """Ma'lumotlar bazasi javob beryaptimi? Xato bo'lsa — sababi, aks holda None."""
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return None
    except Exception as e:  # noqa: BLE001
        return str(e)[:200] or type(e).__name__


async def collect_status() -> dict:
    odoo = await _odoo.diagnose()
    db_error = await check_database()
    polling_ok, polling = runtime.polling_health() if runtime.polling_health else (True, {"polling": "noma'lum"})
    backup = latest_backup()
    try:
        free_gb = shutil.disk_usage(settings.STORAGE_DIR).free / 1024 ** 3
    except OSError:
        free_gb = None

    problems = []
    if not polling_ok:
        problems.append("Telegram'dan xabar olish to'xtagan")
    if not odoo.get("aloqa"):
        problems.append(f"Odoo: {odoo.get('sabab') or 'aloqa yoq'}")
    if db_error:
        problems.append(f"Ma'lumotlar bazasi: {db_error}")
    if backup is None:
        problems.append(f"Zaxira nusxa topilmadi ({settings.BACKUP_DIR})")
    elif backup[1] > BACKUP_MAX_AGE_HOURS:
        problems.append(f"Oxirgi zaxira nusxa {backup[1]:.0f} soat oldin olingan")
    if free_gb is not None and free_gb < DISK_MIN_FREE_GB:
        problems.append(f"Diskda bo'sh joy kam: {free_gb:.1f} GB")

    return {
        "version": runtime.code_version(),
        "uptime": runtime.uptime_seconds(),
        "polling_ok": polling_ok,
        "polling": polling,
        "odoo": odoo,
        "odoo_test": is_test_odoo(),
        "db_error": db_error,
        "backup": backup,
        "free_gb": free_gb,
        "alerts": bool(settings.ALERT_CHAT_ID),
        "today": metrics.totals(1),
        "week": metrics.totals(7),
        "problems": problems,
    }


def _counters_line(c: dict) -> str:
    parts = [f"{c.get('questions', 0)} savol", f"{c.get('tasks_created', 0)} vazifa",
             f"{c.get('reports', 0)} fayl", f"{c.get('errors', 0)} xato"]
    if c.get("triage_fallbacks"):
        parts.append(f"{c['triage_fallbacks']} marta zaxira AI")
    if c.get("odoo_errors"):
        parts.append(f"{c['odoo_errors']} marta Odoo uzilishi")
    return ", ".join(parts)


def _cache_share(c: dict) -> str:
    total = c.get("tokens_in", 0)
    return f"{c.get('tokens_cached', 0) / total * 100:.0f}%" if total else "—"


def format_status(status: dict, *, title: str = "Tizim holati") -> str:
    """Holatni Telegram (HTML) xabariga aylantiradi."""
    e = lambda value: html.escape(str(value), quote=False)  # noqa: E731
    ok, bad = "✅", "❌"
    odoo = status["odoo"]
    lines = [f"<b>{e(title)}</b>", ""]

    lines.append(f"{ok} Bot ishlayapti: {_duration(status['uptime'])} (versiya {e(status['version'])})")
    polling = status["polling"]
    since = polling.get("seconds_since_last_poll")
    lines.append(f"{ok if status['polling_ok'] else bad} Telegram: {e(polling.get('polling'))}"
                 + (f", oxirgi so'rov {since} soniya oldin" if since is not None else ""))

    if odoo.get("aloqa"):
        lines.append(f"{ok} Odoo: {e(odoo.get('server'))} (versiya {e(odoo.get('odoo_versiya'))}, "
                     f"javob {odoo.get('javob_vaqti_ms')} ms)")
    else:
        lines.append(f"{bad} Odoo: {e(odoo.get('server'))} — {e(odoo.get('sabab'))}")
        if odoo.get("tavsiya"):
            lines.append(f"    {e(odoo['tavsiya'])}")
    if status["odoo_test"]:
        lines.append("⚠️ Bu Odoo'ning SINOV bazasi (.dev.odoo.com): qayta qurilganda manzili almashadi "
                     "va bot yozgan vazifalar yo'qoladi. Production uchun asosiy bazaga ulang.")

    lines.append(f"{bad} Ma'lumotlar bazasi: {e(status['db_error'])}" if status["db_error"]
                 else f"{ok} Ma'lumotlar bazasi")

    backup = status["backup"]
    if backup is None:
        lines.append(f"{bad} Zaxira nusxa topilmadi")
    else:
        taken = datetime.fromtimestamp(backup[0].stat().st_mtime, _TZ).strftime("%Y-%m-%d %H:%M")
        fresh = backup[1] <= BACKUP_MAX_AGE_HOURS
        lines.append(f"{ok if fresh else bad} Oxirgi zaxira nusxa: {taken}")
    if status["free_gb"] is not None:
        enough = status["free_gb"] >= DISK_MIN_FREE_GB
        lines.append(f"{ok if enough else bad} Diskda bo'sh joy: {status['free_gb']:.0f} GB")
    lines.append(f"{ok} Xato ogohlantirishlari yoqilgan" if status["alerts"]
                 else "⚠️ Xato ogohlantirishlari o'chiq (.env da ALERT_CHAT_ID yo'q)")

    lines += ["", f"AI: Claude {e(settings.ANTHROPIC_MODEL)} · tahlil va ovoz: OpenAI {e(settings.OPENAI_CHAT_MODEL)}",
              f"Bugun: {_counters_line(status['today'])}",
              f"7 kun: {_counters_line(status['week'])}",
              f"Kesh ulushi (7 kun): {_cache_share(status['week'])}"]
    return "\n".join(lines)
