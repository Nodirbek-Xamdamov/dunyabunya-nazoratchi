"""P1-6 + P1-4: har kuni ertalab (09:00, Asia/Tashkent) ikkita tekshiruv,
BITTA Odoo so'rovi (`_fetch_bot_task_rows`) asosida:

- P1-6: muddati o'tgan/juda uzoq ochiq qolgan vazifalar — CEOning shaxsiy
  chatiga yagona (ro'yxat) eslatma xabari.
- P1-4: hech kimga biriktirilmagan (mas'ulsiz) ochiq vazifalar — asosiy
  guruhning General mavzusiga qisqa eslatma (boshqa "vazifa yaratildi"
  bildirishnomalari bilan bir xil naqshda — `_notify_general`).

Ikkalasida ham hali "Bajarildi"/"Bekor qilindi" bosqichiga o'tmagan
vazifalar ko'rib chiqiladi. Alohida "allaqachon eslatildi" xotirasi YO'Q —
har kuni qayta so'raladi: vazifa hali ochiq bo'lsa qayta ko'rinadi (talab:
"yakunlanmaguncha HAR KUNI takrorlanadi"), "Bajarildi"/mas'ul biriktirilgach
esa navbatdagi tekshiruvda o'zi tushib qoladi.

DIQQAT (P1-4 qamrovi haqida eslatma): task_log jurnali vazifa ASLIDA "ism
aytilib topilmadi" sababli mas'ulsiz qolganmi yoki CEO ATAYLAB mas'ulsiz
yaratganmi — buni FARQLAMAYDI (bunday ma'lumot hech qayerda saqlanmaydi).
Shuning uchun bu yerda oddiyroq, lekin baribir foydali mezon ishlatiladi:
Odoo'da HALI HAM hech kimga biriktirilmagan, ochiq turgan har qanday bot
yaratgan vazifa haqida xabar beriladi — sababidan qat'iy nazar CEO uchun
diqqat talab qiladigan holat."""

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
from app.telegram.tg_format import split_message

log = logging.getLogger(__name__)

_odoo = OdooClient()
_TZ = ZoneInfo("Asia/Tashkent")
_REMINDER_TIME = time(hour=9, minute=0)

_DONE_OR_CANCELLED = {
    "bajarildi", "bajarilgan", "tugallandi", "done",
    "bekor qilindi", "bekor qilingan", "cancelled", "canceled",
}
# Odoo'ning o'z holat maydoni (state): vazifa Odoo interfeysida bosqichi o'zgartirilmasdan
# "Done"/"Cancelled" qilib yopilgan bo'lishi ham mumkin
_CLOSED_STATES = {"1_done", "1_canceled", "done", "cancelled", "canceled"}


def _is_open(row: dict) -> bool:
    return (_stage_name(row).casefold() not in _DONE_OR_CANCELLED
            and str(row.get("state") or "").casefold() not in _CLOSED_STATES)


def _in_logged_project(row: dict, logged_project_id: int | None) -> bool:
    """Odoo'dagi vazifa bot uni yaratgan loyihada turibdimi?

    task_log'da ESKI Odoo instansiyasida yaratilgan vazifalar ham bor. Odoo almashgach,
    o'sha raqamlar (masalan #187) yangi bazada butunlay BOSHQA vazifalarga to'g'ri keladi —
    loyiha solishtirilmasa, CEO'ga o'zi hech qachon bermagan begona vazifalar "hali
    bajarilmadi" deb eslatilardi."""
    if logged_project_id is None:
        return True
    project = row.get("project_id")
    return isinstance(project, (list, tuple)) and bool(project) and project[0] == logged_project_id


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


def _department_name(row: dict) -> str:
    project = row.get("project_id")
    return project[1] if isinstance(project, (list, tuple)) and len(project) > 1 else ""


async def _fetch_bot_task_rows() -> tuple[list[dict], dict[int, str]]:
    """Bot yaratgan (task_log jurnalidagi), hali OCHIQ (Bajarildi/Bekor
    qilindi emas) barcha vazifalarning xom Odoo qatorlarini va mas'ullar
    ismlari lug'atini qaytaradi — P1-6/P1-4 shu ustida alohida filtrlaydi."""
    async with SessionLocal() as session:
        logs = await get_recent_task_logs(session)
    if not logs:
        return [], {}
    # Jurnal eng yangisidan boshlab keladi — har vazifa uchun so'nggi yozuvdagi loyiha olinadi
    logged_project: dict[int, int | None] = {}
    for entry in logs:
        logged_project.setdefault(entry.odoo_task_id, getattr(entry, "odoo_project_id", None))

    rows = await _odoo.execute_kw(
        "project.task", "search_read",
        [[["id", "in", list(logged_project)]]],
        {"fields": ["id", "name", "date_deadline", "create_date", "stage_id", "state",
                    "user_ids", "project_id"]},
    )
    rows = [r for r in rows if _is_open(r) and _in_logged_project(r, logged_project.get(r["id"]))]
    if not rows:
        return [], {}

    # Bir so'rovda barcha mas'ullar ismini olish (har vazifa uchun alohida
    # res.users so'rovi o'rniga)
    all_user_ids = sorted({uid for r in rows for uid in (r.get("user_ids") or [])})
    user_names: dict[int, str] = {}
    if all_user_ids:
        users = await _odoo.execute_kw(
            "res.users", "search_read", [[["id", "in", all_user_ids]]], {"fields": ["id", "name"]},
        )
        user_names = {u["id"]: u["name"] for u in users}

    return rows, user_names


async def _find_overdue_tasks(fetched: tuple[list[dict], dict[int, str]] | None = None) -> list[dict]:
    """P1-6: hali ochiq va muddati o'tgan/eskirgan vazifalar. Har biri:
    {task_id, title, department, assignee_names, deadline (date|None),
    days_since_created}. `fetched` — tayyor `_fetch_bot_task_rows()` natijasi
    (kunlik tekshiruv Odoo'dan bir marta o'qib, ikkala hisobotga beradi)."""
    rows, user_names = fetched if fetched is not None else await _fetch_bot_task_rows()
    today = datetime.now(_TZ).date()

    overdue = []
    for r in rows:
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

        assignee_ids = r.get("user_ids") or []
        assignee_names = [user_names[uid] for uid in assignee_ids if uid in user_names]

        overdue.append({
            "task_id": r["id"],
            "title": r.get("name") or f"#{r['id']}",
            "department": _department_name(r),
            "assignee_names": assignee_names,
            "deadline": deadline,
            "days_since_created": days_since_created,
        })

    return overdue


async def _find_unassigned_tasks(fetched: tuple[list[dict], dict[int, str]] | None = None) -> list[dict]:
    """P1-4: hali ochiq va hech kimga biriktirilmagan (user_ids bo'sh)
    vazifalar. Har biri: {task_id, title, department}."""
    rows, _user_names = fetched if fetched is not None else await _fetch_bot_task_rows()
    return [
        {"task_id": r["id"], "title": r.get("name") or f"#{r['id']}", "department": _department_name(r)}
        for r in rows if not r.get("user_ids")
    ]


def _format_reminder_message(tasks: list[dict]) -> str:
    lines = ["Assalamu alaykum, quyidagi vazifalar hali bajarilmadi:", ""]
    for t in tasks:
        assignee = ", ".join(t["assignee_names"]) if t["assignee_names"] else "biriktirilmagan (mas'ulsiz)"
        if t["deadline"] is not None:
            when = f"muddati {t['deadline'].isoformat()} edi"
        else:
            when = f"{t['days_since_created']} kun oldin berilgan"
        dept = f" ({t['department']})" if t["department"] else ""
        lines.append(f"📌 «{t['title']}»{dept} — mas'ul: {assignee}; {when}, hali bajarilmadi.")
    return "\n".join(lines)


def _format_unassigned_message(tasks: list[dict]) -> str:
    lines = [f"ℹ️ {len(tasks)} ta vazifa hali hech kimga biriktirilmagan:", ""]
    for t in tasks:
        dept = f" ({t['department']})" if t["department"] else ""
        lines.append(f"📌 «{t['title']}»{dept}")
    return "\n".join(lines)


async def _send_long(bot, *, chat_id: int, text: str, message_thread_id: int | None = None) -> None:
    """Uzun ro'yxatni Telegram chegarasiga (4096 belgi) sig'adigan bo'laklarga bo'lib yuboradi —
    aks holda vazifalar ko'payganda xabar umuman ketmay, eslatma jimgina to'xtab qolardi."""
    for part in split_message(text):
        kwargs = {"chat_id": chat_id, "text": part}
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        await bot.send_message(**kwargs)


async def run_task_reminder_check(bot, fetched=None) -> str | None:
    """P1-6: bir marta tekshiruv ishga tushiradi — topilgan eslatmalarni
    CEOning shaxsiy chatiga yuboradi. Hech narsa topilmasa yoki CEO chat_id
    hali ma'lum bo'lmasa — hech narsa yubormaydi (None qaytaradi)."""
    tasks = await (_find_overdue_tasks(fetched) if fetched is not None else _find_overdue_tasks())
    if not tasks:
        return None

    ceo_ids = await ceo_state.get_ceo_telegram_user_ids()
    if not ceo_ids:
        log.warning("task_reminders: %d ta muddati o'tgan vazifa bor, lekin CEO "
                     "telegram_user_id hali aniqlanmagan — eslatma yuborilmadi", len(tasks))
        return None

    message = _format_reminder_message(tasks)
    sent = 0
    for ceo_id in ceo_ids:  # bir nechta CEO bo'lishi mumkin — har biriga alohida
        try:
            await _send_long(bot, chat_id=ceo_id, text=message)
            sent += 1
        except Exception:
            log.exception("task_reminders: CEO %s ga eslatma yuborishda xato", ceo_id)
    if not sent:
        return None

    log.info("task_reminders: %d ta CEOga %d ta muddati o'tgan vazifa haqida eslatma yuborildi",
             sent, len(tasks))
    return message


async def run_unassigned_task_digest(bot, fetched=None) -> str | None:
    """P1-4: bir marta tekshiruv ishga tushiradi — hali mas'ulsiz qolgan
    ochiq vazifalarni asosiy guruhning General mavzusiga xabar qiladi.
    Hech narsa topilmasa — hech narsa yubormaydi (None qaytaradi)."""
    tasks = await (_find_unassigned_tasks(fetched) if fetched is not None else _find_unassigned_tasks())
    if not tasks:
        return None

    message = _format_unassigned_message(tasks)
    try:
        for part in split_message(message):
            await bot.send_message(chat_id=settings.TELEGRAM_GROUP_ID, message_thread_id=None, text=part)
    except Exception:
        log.exception("task_reminders: General mavzuga mas'ulsiz vazifalar xabarini yuborishda xato")
        return None

    log.info("task_reminders: General mavzuga %d ta mas'ulsiz vazifa haqida xabar yuborildi", len(tasks))
    return message


# ─── Rejalashtiruvchi (memory_digest.py bilan bir xil naqsh) ───────────────

def _next_run(target_time: time) -> datetime:
    now = datetime.now(_TZ)
    target = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


async def run_daily_checks(bot) -> None:
    """Kunlik tekshiruv: Odoo'dan BIR MARTA o'qib, ikkala eslatmani yuboradi.
    P1-6 (CEO DM) va P1-4 (General mavzu) bir-biriga bog'liq EMAS — biri xato
    bersa ham ikkinchisi ishlayveradi."""
    fetched = await _fetch_bot_task_rows()
    results = await asyncio.gather(
        run_task_reminder_check(bot, fetched),
        run_unassigned_task_digest(bot, fetched),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            log.error("task_reminders: kunlik eslatma yuborilmadi: %s", result)


async def _reminder_loop(bot) -> None:
    log.info("task_reminders: rejalashtiruvchi ishga tushdi (har kuni %s, Asia/Tashkent)", _REMINDER_TIME)
    while True:
        target = _next_run(_REMINDER_TIME)
        try:
            await asyncio.sleep(max(0.0, (target - datetime.now(_TZ)).total_seconds()))
            await run_daily_checks(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("task_reminders: kunlik tekshiruvda xato")
        await asyncio.sleep(60)


def start_task_reminder_scheduler(bot) -> asyncio.Task:
    """main.py lifespan ichida chaqiriladi — fon vazifasini boshlaydi."""
    return asyncio.create_task(_reminder_loop(bot))
