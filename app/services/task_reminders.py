"""P1-6: muddati o'tgan/juda uzoq ochiq qolgan vazifalar haqida CEOga
har kuni ertalab (09:00, Asia/Tashkent) yagona (ro'yxat) eslatma xabari.

Ikki holat eslatiladi (ikkalasi ham hali "Bajarildi"/"Bekor qilindi"
bosqichiga o'tmagan bo'lishi shart):
1. Aniq muddatli vazifa — muddat kuni allaqachon o'tgan;
2. Muddatsiz vazifa — berilganiga TASK_REMINDER_STALE_DAYS (sukut 3) kundan
   ortiq vaqt o'tgan.

Alohida "allaqachon eslatildi" xotirasi YO'Q — har kuni qayta so'raladi:
vazifa hali ochiq bo'lsa qayta ko'rinadi (talab: "yakunlanmaguncha HAR KUNI
takrorlanadi"), "Bajarildi" bosilgach esa navbatdagi tekshiruvda o'zi
tushib qoladi (Odoo'dagi bosqichi endi "Bajarildi" bo'lgani uchun)."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app import ceo_state
from app.config import settings
from app.db.repo import get_recent_task_logs
from app.db.session import SessionLocal
from app.services.odoo import OdooClient

log = logging.getLogger(__name__)

_odoo = OdooClient()
_TZ = ZoneInfo("Asia/Tashkent")
_REMINDER_TIME = time(hour=9, minute=0)

_DONE_OR_CANCELLED = {
    "bajarildi", "bajarilgan", "tugallandi", "done",
    "bekor qilindi", "bekor qilingan", "cancelled", "canceled",
}


def _stage_name(row: dict) -> str:
    stage = row.get("stage_id")
    if isinstance(stage, (list, tuple)) and len(stage) > 1:
        return str(stage[1] or "").strip()
    return ""


def _parse_odoo_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


async def _find_overdue_tasks() -> list[dict]:
    """Bot yaratgan, hali ochiq (Bajarildi/Bekor qilindi emas) va muddati
    o'tgan/eskirgan vazifalarni topadi. Har biri: {task_id, title, department,
    assignee_names, deadline (date|None), days_since_created}."""
    async with SessionLocal() as session:
        logs = await get_recent_task_logs(session)
    if not logs:
        return []
    task_ids = list(dict.fromkeys(l.odoo_task_id for l in logs))

    rows = await _odoo.execute_kw(
        "project.task", "search_read",
        [[["id", "in", task_ids]]],
        {"fields": ["id", "name", "date_deadline", "create_date", "stage_id", "user_ids", "project_id"]},
    )
    if not rows:
        return []

    # Bir so'rovda barcha mas'ullar ismini olish (har vazifa uchun alohida
    # res.users so'rovi o'rniga)
    all_user_ids = sorted({uid for r in rows for uid in (r.get("user_ids") or [])})
    user_names: dict[int, str] = {}
    if all_user_ids:
        users = await _odoo.execute_kw(
            "res.users", "search_read", [[["id", "in", all_user_ids]]], {"fields": ["id", "name"]},
        )
        user_names = {u["id"]: u["name"] for u in users}

    today = datetime.now(_TZ).date()

    overdue = []
    for r in rows:
        if _stage_name(r).casefold() in _DONE_OR_CANCELLED:
            continue

        deadline = _parse_odoo_date(r.get("date_deadline"))
        created = _parse_odoo_date(r.get("create_date"))
        is_overdue = False
        days_since_created = None

        if deadline is not None:
            is_overdue = deadline < today
        elif created is not None:
            days_since_created = (today - created).days
            is_overdue = days_since_created >= settings.TASK_REMINDER_STALE_DAYS

        if not is_overdue:
            continue

        project = r.get("project_id")
        department = project[1] if isinstance(project, (list, tuple)) and len(project) > 1 else ""
        assignee_ids = r.get("user_ids") or []
        assignee_names = [user_names[uid] for uid in assignee_ids if uid in user_names]

        overdue.append({
            "task_id": r["id"],
            "title": r.get("name") or f"#{r['id']}",
            "department": department,
            "assignee_names": assignee_names,
            "deadline": deadline,
            "days_since_created": days_since_created,
        })

    return overdue


def _format_reminder_message(tasks: list[dict]) -> str:
    lines = ["Assalamu alaykum, quyidagi vazifalar hali bajarilmadi:", ""]
    for t in tasks:
        assignee = ", ".join(t["assignee_names"]) if t["assignee_names"] else "hech kim (mas'ulsiz)"
        if t["deadline"] is not None:
            when = f"{t['deadline'].isoformat()} sanasigacha"
        else:
            when = f"{t['days_since_created']} kun oldin berilib, shu kunlargacha"
        dept = f" ({t['department']})" if t["department"] else ""
        lines.append(f"📌 Siz {assignee}ga bergan «{t['title']}»{dept} vazifasi {when} bajarilishi kerak edi, hali bajarilmadi.")
    return "\n".join(lines)


async def run_task_reminder_check(bot) -> str | None:
    """Bir marta tekshiruv ishga tushiradi — topilgan eslatmalarni CEOning
    shaxsiy chatiga yuboradi. Hech narsa topilmasa yoki CEO chat_id hali
    ma'lum bo'lmasa — hech narsa yubormaydi (None qaytaradi)."""
    tasks = await _find_overdue_tasks()
    if not tasks:
        return None

    ceo_id = await ceo_state.get_ceo_telegram_user_id()
    if not ceo_id:
        log.warning("task_reminders: %d ta muddati o'tgan vazifa bor, lekin CEO "
                     "telegram_user_id hali aniqlanmagan — eslatma yuborilmadi", len(tasks))
        return None

    message = _format_reminder_message(tasks)
    try:
        await bot.send_message(chat_id=ceo_id, text=message)
    except Exception:
        log.exception("task_reminders: CEOga eslatma yuborishda xato")
        return None

    log.info("task_reminders: CEOga %d ta muddati o'tgan vazifa haqida eslatma yuborildi", len(tasks))
    return message


# ─── Rejalashtiruvchi (memory_digest.py bilan bir xil naqsh) ───────────────

def _next_run(target_time: time) -> datetime:
    now = datetime.now(_TZ)
    target = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


async def _reminder_loop(bot) -> None:
    log.info("task_reminders: rejalashtiruvchi ishga tushdi (har kuni %s, Asia/Tashkent)", _REMINDER_TIME)
    while True:
        target = _next_run(_REMINDER_TIME)
        try:
            await asyncio.sleep(max(0.0, (target - datetime.now(_TZ)).total_seconds()))
            await run_task_reminder_check(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("task_reminders: kunlik tekshiruvda xato")
        await asyncio.sleep(60)


def start_task_reminder_scheduler(bot) -> asyncio.Task:
    """main.py lifespan ichida chaqiriladi — fon vazifasini boshlaydi."""
    return asyncio.create_task(_reminder_loop(bot))
