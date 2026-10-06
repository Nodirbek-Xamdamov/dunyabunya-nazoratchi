"""Yordamchining vazifa amallari: yaratish, yangilash, izoh qoldirish (Odoo project.task).

Natija guruhdagi bo'lim mavzusiga yozilgan vazifa bilan bir xil: vazifa bo'lim loyihasiga
yoziladi, jurnalga (task_log) tushadi va mas'ul xodimga Telegram orqali xabar boradi. Farqi —
rahbar buni shaxsiy chatda yoki General mavzusida oddiy gap bilan so'ray oladi.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from difflib import get_close_matches
from zoneinfo import ZoneInfo

from app import employee_store, metrics, runtime
from app.config import settings
from app.db import repo
from app.db.session import SessionLocal
from app.services import agent_context
from app.services.agent_tools import ToolInputError
from app.services.odoo import OdooClient

log = logging.getLogger(__name__)

_odoo = OdooClient()
_TZ = ZoneInfo("Asia/Tashkent")
_CLEAR_LEAD_GAP = 10  # eng yaqin nomzod keyingisidan shuncha ball oldinda bo'lsa — yagona deb olinadi
_TASK_FIELDS = ["id", "name", "project_id", "user_ids", "date_deadline", "state", "stage_id"]


def task_url(task_id: int) -> str:
    return f"{settings.ODOO_URL.rstrip('/')}/web#id={task_id}&model=project.task&view_type=form"


async def _topics() -> list:
    async with SessionLocal() as session:
        return await repo.list_topics(session)


def _match_department(topics: list, query: str):
    wanted = (query or "").strip().casefold()
    by_name = {t.name.casefold(): t for t in topics}
    if wanted in by_name:
        return by_name[wanted]
    partial = [t for name, t in by_name.items() if wanted and (name.startswith(wanted) or wanted.startswith(name))]
    if len(partial) == 1:
        return partial[0]
    close = get_close_matches(wanted, list(by_name), n=1, cutoff=0.75)
    return by_name[close[0]] if close else None


def _deadline(value: str) -> str | None:
    """Muddat: "YYYY-MM-DD" (o'zgarishsiz) yoki "YYYY-MM-DD HH:MM" (Toshkent vaqti -> UTC)."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value).isoformat()
        local = datetime.strptime(value.replace("T", " ")[:16], "%Y-%m-%d %H:%M").replace(tzinfo=_TZ)
    except ValueError:
        raise ToolInputError("deadline formati: YYYY-MM-DD yoki YYYY-MM-DD HH:MM (Toshkent vaqti)") from None
    return local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def _find_assignee(name: str) -> list[dict]:
    """Ism bo'yicha nomzodlar: avval botda ro'yxatdan o'tganlar, keyin Odoo foydalanuvchilari
    (login bor — vazifaga rasman biriktiriladi), topilmasa xodimlar ro'yxati (login yo'q)."""
    seen: set[int] = set()
    local = []
    for emp in await employee_store.get_employees_by_name(name):
        if emp.odoo_user_id and emp.odoo_user_id not in seen:
            seen.add(emp.odoo_user_id)
            local.append({"id": emp.odoo_user_id, "name": emp.odoo_user_name or emp.full_name,
                          "score": 100.0, "source": "res.users"})
    if local:
        return local
    users = await _odoo.find_users_by_name(name)
    if users:
        return [{**u, "source": "res.users"} for u in users]
    return [{**e, "source": "hr.employee"} for e in await _odoo.find_employees_by_name(name)]


async def _resolve_assignee(assignee: str, assignee_user_id: int) -> tuple[dict | None, dict | None]:
    """(tanlangan nomzod, aniqlashtirish so'rovi). Ikkalasi ham None — mas'ul ko'rsatilmagan."""
    if assignee_user_id:
        rows = await _odoo.execute_kw("res.users", "read", [[int(assignee_user_id)], ["name"]])
        if not rows:
            raise ToolInputError(f"assignee_user_id={assignee_user_id} bo'yicha Odoo foydalanuvchisi topilmadi")
        return {"id": rows[0]["id"], "name": rows[0]["name"], "source": "res.users"}, None
    if not (assignee or "").strip():
        return None, None
    found = await _find_assignee(assignee.strip())
    if not found:
        return None, {"aniqlashtirish_kerak": True,
                      "xabar": f"\"{assignee}\" ismli xodim Odoo'da topilmadi. Foydalanuvchidan to'liq ism-familiyani "
                               "so'rang yoki vazifani mas'ulsiz yaratishga roziligini oling."}
    if len(found) > 1 and found[0].get("score", 0) - found[1].get("score", 0) < _CLEAR_LEAD_GAP:
        options = [{"ism": c["name"], **({"assignee_user_id": c["id"]} if c["source"] == "res.users" else {})}
                   for c in found[:8]]
        return None, {"aniqlashtirish_kerak": True, "nomzodlar": options,
                      "xabar": f"\"{assignee}\" bo'yicha {len(found)} ta xodim topildi. Foydalanuvchidan qaysi biri "
                               "ekanini so'rang; javobdan keyin assignee_user_id (bor bo'lsa) yoki to'liq ism bilan "
                               "qayta chaqiring. O'zingiz tanlamang."}
    return found[0], None


async def _notify(task_id: int, title: str, description: str, department: str, chosen: dict) -> str:
    notifier = runtime.task_notifier
    if notifier is None:
        return "yuborilmadi (bot xabar yuborish rejimida emas)"
    try:
        return await notifier(
            task_id=task_id, title=title, description=description, topic_name=department,
            odoo_user_id=chosen["id"] if chosen["source"] == "res.users" else None,
            assignee_name=chosen["name"],
        )
    except Exception as e:  # noqa: BLE001 — vazifa allaqachon yaratilgan, xabar ikkinchi darajali
        log.warning("vazifa #%s: mas'ulga xabar yuborib bo'lmadi: %s", task_id, e)
        return "yuborilmadi (Telegram xatosi)"


async def create_task(title: str, department: str, description: str = "", assignee: str = "",
                      assignee_user_id: int = 0, deadline: str = "") -> dict:
    title = " ".join((title or "").split())[:200]
    if not title:
        raise ToolInputError("title bo'sh — vazifaning qisqa nomini bering")
    topics = await _topics()
    topic = _match_department(topics, department)
    if topic is None:
        return {"error": f"\"{department}\" degan bo'lim yo'q.", "mavjud_bolimlar": [t.name for t in topics],
                "xabar": "Vazifa mazmuniga mos bo'limni shu ro'yxatdan tanlang; mos kelmasa foydalanuvchidan so'rang."}
    due = _deadline(deadline)
    chosen, question = await _resolve_assignee(assignee, assignee_user_id)
    if question:
        return question

    can_assign = bool(chosen and chosen["source"] == "res.users")
    task_id = await _odoo.create_task(
        project_id=topic.odoo_project_id, name=title, description=description or "",
        user_ids=[chosen["id"]] if can_assign else None, date_deadline=due,
    )
    metrics.inc("tasks_created")
    who = agent_context.current.get()
    try:
        async with SessionLocal() as session:
            # Mavzu ko'rsatilmaydi: "bajarildi" xabari vazifa so'ralgan chatning o'ziga qaytadi
            await repo.save_task_log(
                session, odoo_task_id=task_id, odoo_project_id=topic.odoo_project_id,
                telegram_chat_id=who.chat_id if who else None, telegram_topic_id=who.thread_id if who else None,
                telegram_message_id=None, title=title,
                created_by_telegram_user_id=who.telegram_user_id if who else None,
            )
    except Exception:
        log.exception("task_log yozib bo'lmadi (odoo_task_id=%s)", task_id)

    result: dict = {"yaratildi": True, "task_id": task_id, "nomi": title, "bolim": topic.name,
                    "muddat": deadline or None, "havola": task_url(task_id)}
    if chosen:
        result["masul"] = chosen["name"]
        if not can_assign:
            result["masul_izoh"] = ("Bu xodimda Odoo login hisobi yo'q — vazifa Odoo'da rasman biriktirilmadi "
                                    "(mas'ulsiz turibdi).")
        result["xodimga_xabar"] = await _notify(task_id, title, description or "", topic.name, chosen)
    return result


async def _task(task_id: int) -> dict:
    rows = await _odoo.execute_kw("project.task", "read", [[int(task_id)], _TASK_FIELDS])
    if not rows:
        raise ToolInputError(f"#{task_id} vazifa Odoo'da topilmadi")
    return rows[0]


async def update_task(task_id: int, title: str = "", description: str = "", deadline: str = "",
                      status: str = "", assignee: str = "", assignee_user_id: int = 0) -> dict:
    task = await _task(task_id)
    vals: dict = {}
    changed: list[str] = []
    if title.strip():
        vals["name"] = " ".join(title.split())[:200]
        changed.append("nomi")
    if description.strip():
        vals["description"] = description
        changed.append("tavsifi")
    if deadline.strip():
        vals["date_deadline"] = False if deadline.strip().casefold() in ("yo'q", "none", "-") else _deadline(deadline)
        changed.append("muddati")
    if assignee.strip() or assignee_user_id:
        chosen, question = await _resolve_assignee(assignee, assignee_user_id)
        if question:
            return question
        if chosen["source"] != "res.users":
            return {"error": f"{chosen['name']}da Odoo login hisobi yo'q — vazifani unga rasman biriktirib bo'lmaydi."}
        vals["user_ids"] = [(6, 0, [chosen["id"]])]
        changed.append(f"mas'uli ({chosen['name']})")
    if vals:
        await _odoo.execute_kw("project.task", "write", [[int(task_id)], vals])

    status = (status or "").strip().casefold()
    if status in ("done", "cancel"):
        await _odoo.update_task_stage(int(task_id), done=status == "done")
        changed.append("holati: bajarildi" if status == "done" else "holati: bekor qilindi")
    elif status == "reopen":
        stage_id = await _odoo._ensure_working_stage(task["project_id"][0]) if task.get("project_id") else None
        reopen = {"state": "01_in_progress", **({"stage_id": stage_id} if stage_id else {})}
        await _odoo.execute_kw("project.task", "write", [[int(task_id)], reopen])
        changed.append("holati: qayta ochildi")
    elif status:
        raise ToolInputError("status qiymatlari: done, cancel, reopen")

    if not changed:
        return {"error": "O'zgartiriladigan narsa ko'rsatilmadi (title, description, deadline, status yoki assignee)."}
    return {"yangilandi": True, "task_id": int(task_id), "nomi": vals.get("name") or task["name"],
            "ozgardi": changed, "havola": task_url(int(task_id))}


async def add_task_comment(task_id: int, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ToolInputError("text bo'sh")
    task = await _task(task_id)
    await _odoo.execute_kw("project.task", "message_post", [[int(task_id)]], {"body": text[:4000]})
    return {"izoh_qoshildi": True, "task_id": int(task_id), "nomi": task["name"], "havola": task_url(int(task_id))}


TOOLS = {"create_task": create_task, "update_task": update_task, "add_task_comment": add_task_comment}

SCHEMAS = {
    "create_task": {
        "name": "create_task",
        "description": (
            "Odoo'da yangi vazifa yaratadi va mas'ul xodimga Telegram orqali xabar yuboradi. Foydalanuvchi "
            "birovga ish topshirsa (\"...ga ayt\", \"...ga vazifa ber\", \"... qilsin\", \"topshiriq yoz\") shu "
            "toolni chaqiring. Bo'limni vazifa mazmunidan o'zingiz tanlang. Mas'ul va muddat faqat foydalanuvchi "
            "aytgan bo'lsa beriladi — o'zingizdan qo'shmang. Tool \"aniqlashtirish_kerak\" qaytarsa — vazifa "
            "yaratilmagan: foydalanuvchidan so'rang va javobidan keyin qayta chaqiring."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Vazifaning qisqa nomi (bir jumla)"},
                "department": {"type": "string", "description": "Bo'lim nomi: Sotuv, Moliya, Ombor, HR, Marketing, CRM, "
                                                                "Logistika, Taminot (aniq ro'yxatni tool xatoda qaytaradi)"},
                "description": {"type": "string", "description": "Batafsil tavsif (ixtiyoriy)"},
                "assignee": {"type": "string", "description": "Mas'ul xodim ismi — foydalanuvchi aytganidek"},
                "assignee_user_id": {"type": "integer", "description": "Nomzodlar ichidan foydalanuvchi tanlagan xodimning ID'si "
                                                                       "(faqat tool oldin \"nomzodlar\" qaytargan bo'lsa)"},
                "deadline": {"type": "string", "description": "Muddat: YYYY-MM-DD yoki YYYY-MM-DD HH:MM (Toshkent vaqti)"},
            },
            "required": ["title", "department"],
        },
    },
    "update_task": {
        "name": "update_task",
        "description": (
            "Mavjud vazifani o'zgartiradi: nomi, tavsifi, muddati, mas'uli yoki holati (done — bajarildi, "
            "cancel — bekor qilindi, reopen — qayta ochish). Vazifa raqami (task_id) noma'lum bo'lsa avval "
            "odoo_search_read (model: project.task) bilan toping. Vazifani O'CHIRISH bu tool bilan qilinmaydi — "
            "buning uchun vazifa xabari ostidagi «O'chirish» tugmasi yoki /otask <raqam> buyrug'i bor."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "deadline": {"type": "string", "description": "YYYY-MM-DD yoki YYYY-MM-DD HH:MM; muddatni olib tashlash uchun \"yo'q\""},
                "status": {"type": "string", "enum": ["done", "cancel", "reopen"]},
                "assignee": {"type": "string"},
                "assignee_user_id": {"type": "integer"},
            },
            "required": ["task_id"],
        },
    },
    "add_task_comment": {
        "name": "add_task_comment",
        "description": "Vazifaning Odoo'dagi muhokamasiga (chatter) izoh yozadi.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["task_id", "text"],
        },
    },
}
