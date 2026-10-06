"""Eslatmalar va rejalashtirilgan so'rovlar.

Rahbar "ertaga 9 da eslat: bank bilan uchrashuv" yoki "har kuni 18:00 da bugungi sotuvni
yubor" desa yordamchi shu yerga yozib qo'yadi. Ikki turi bor:
- oddiy eslatma — vaqti kelganda matnning o'zi yuboriladi;
- so'rov (`ask`) — vaqti kelganda matn yordamchiga savol sifatida beriladi va javobi yuboriladi.

`storage/reminders.json` da saqlanadi, shuning uchun bot qayta ishga tushsa ham yo'qolmaydi;
bot o'chiq paytda vaqti o'tib ketgan eslatma bot turgach kechikib bo'lsa ham yuboriladi.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.services import agent_context
from app.services.agent_tools import ToolInputError

log = logging.getLogger(__name__)

_PATH = Path(settings.STORAGE_DIR) / "reminders.json"
_TZ = ZoneInfo("Asia/Tashkent")
_FMT = "%Y-%m-%d %H:%M"
_CHECK_SECONDS = 20
MAX_PER_CHAT = 40
REPEATS = {"": "bir marta", "daily": "har kuni", "weekdays": "ish kunlari (dushanba–juma)",
           "weekly": "har hafta", "monthly": "har oy"}
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(_TZ).replace(tzinfo=None, second=0, microsecond=0)


def _read() -> list[dict]:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8")) if _PATH.exists() else []
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        log.warning("reminders: %s o'qib bo'lmadi — bo'sh deb olinadi", _PATH)
        return []


def _write(items: list[dict]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_PATH)


def next_due(due: datetime, repeat: str) -> datetime | None:
    """Takrorlanadigan eslatmaning keyingi vaqti (bir martalik uchun None)."""
    if repeat == "daily":
        return due + timedelta(days=1)
    if repeat == "weekdays":
        nxt = due + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt
    if repeat == "weekly":
        return due + timedelta(days=7)
    if repeat == "monthly":
        year, month = (due.year + 1, 1) if due.month == 12 else (due.year, due.month + 1)
        return due.replace(year=year, month=month, day=min(due.day, calendar.monthrange(year, month)[1]))
    return None


def _describe(item: dict) -> dict:
    return {"id": item["id"], "vaqt": item["due"], "matn": item["text"],
            "takrorlanish": REPEATS.get(item.get("repeat") or "", "bir marta"),
            "turi": "so'rov (javobi yuboriladi)" if item.get("kind") == "ask" else "eslatma"}


# ─── Tool'lar ───────────────────────────────────────────────────────────────

async def set_reminder(when: str, text: str, repeat: str = "", run_as_question: bool = False) -> dict:
    who = agent_context.actor()
    text = " ".join(str(text or "").split())[:600]
    if not text:
        raise ToolInputError("text bo'sh — nimani eslatish kerakligini yozing")
    repeat = (repeat or "").strip().casefold()
    if repeat not in REPEATS:
        raise ToolInputError("repeat qiymatlari: daily, weekdays, weekly, monthly (yoki bo'sh — bir marta)")
    try:
        due = datetime.strptime(str(when).strip().replace("T", " ")[:16], _FMT)
    except ValueError:
        raise ToolInputError("when formati: YYYY-MM-DD HH:MM (Toshkent vaqti)") from None
    now = _now()
    if due < now - timedelta(minutes=1):
        raise ToolInputError(f"Bu vaqt o'tib ketgan (hozir {now.strftime(_FMT)}). Kelajakdagi vaqtni bering.")
    if due > now + timedelta(days=730):
        raise ToolInputError("Eslatma 2 yildan uzoq muddatga qo'yilmaydi")
    chat_id = who.chat_id or who.user_key
    with _lock:
        items = _read()
        if sum(1 for i in items if i["chat_id"] == chat_id) >= MAX_PER_CHAT:
            return {"error": f"Bu chatda eslatmalar soni chegaraga yetdi ({MAX_PER_CHAT}). Avval keraksizini bekor qiling."}
        item = {
            "id": max((i["id"] for i in items), default=0) + 1,
            "chat_id": chat_id, "thread_id": who.thread_id, "user_key": who.user_key, "role": who.role,
            "telegram_user_id": who.telegram_user_id, "text": text, "due": due.strftime(_FMT),
            "repeat": repeat, "kind": "ask" if run_as_question else "note",
        }
        items.append(item)
        _write(items)
    return {"qoyildi": True, **_describe(item)}


async def list_reminders() -> dict:
    who = agent_context.actor()
    chat_id = who.chat_id or who.user_key
    mine = sorted((i for i in _read() if i["chat_id"] == chat_id), key=lambda i: i["due"])
    return {"eslatmalar": [_describe(i) for i in mine], "jami": len(mine)}


async def cancel_reminder(reminder_id: int) -> dict:
    who = agent_context.actor()
    chat_id = who.chat_id or who.user_key
    with _lock:
        items = _read()
        kept = [i for i in items if not (i["id"] == int(reminder_id) and i["chat_id"] == chat_id)]
        if len(kept) == len(items):
            return {"error": f"#{reminder_id} raqamli eslatma bu chatda topilmadi."}
        _write(kept)
    return {"bekor_qilindi": True, "id": int(reminder_id)}


# ─── Vaqti kelganlarni yuborish ─────────────────────────────────────────────

def take_due(now: datetime | None = None) -> list[dict]:
    """Vaqti kelgan eslatmalarni oladi va ro'yxatni darhol yangilaydi (takrorlanadigani keyingi
    vaqtga suriladi, bir martalik o'chiriladi) — yuborish paytida bot qayta ishga tushsa ham
    bitta eslatma ikki marta ketmaydi."""
    now = now or _now()
    stamp = now.strftime(_FMT)
    with _lock:
        items = _read()
        due_items = [dict(i) for i in items if i["due"] <= stamp]
        if not due_items:
            return []
        kept = []
        for item in items:
            if item["due"] > stamp:
                kept.append(item)
                continue
            upcoming = next_due(datetime.strptime(item["due"], _FMT), item.get("repeat") or "")
            while upcoming is not None and upcoming <= now:  # bot uzoq o'chiq turgan — o'tganlarini tashlab o'tamiz
                upcoming = next_due(upcoming, item.get("repeat") or "")
            if upcoming is not None:
                kept.append({**item, "due": upcoming.strftime(_FMT)})
        _write(kept)
    return due_items


async def _deliver(bot, item: dict, now: datetime) -> None:
    late = now - datetime.strptime(item["due"], _FMT) > timedelta(minutes=10)
    suffix = f" (belgilangan vaqt: {item['due']})" if late else ""
    chat_id, thread_id = item["chat_id"], item.get("thread_id")
    if item.get("kind") != "ask":
        await bot.send_message(chat_id=chat_id, message_thread_id=thread_id,
                               text=f"⏰ Eslatma{suffix}:\n{item['text']}")
        return
    # So'rov: matn yordamchiga beriladi va javobi yuboriladi (importlar aylanma bo'lmasligi uchun shu yerda)
    from app.rbac import Role
    from app.services.assistant import ask_with_files
    from app.telegram import delivery

    await bot.send_message(chat_id=chat_id, message_thread_id=thread_id,
                           text=f"⏰ Rejalashtirilgan so'rov{suffix}: {item['text']}")
    try:
        role = Role(item.get("role") or "ceo")
    except ValueError:
        role = Role.NONE
    result = await ask_with_files(
        item["user_key"], item["text"], role=role, actor_id=item.get("telegram_user_id"),
        chat_id=chat_id, thread_id=thread_id, scheduled=True,
    )
    await delivery.send_result(bot, chat_id, thread_id, result)


async def run_due(bot, now: datetime | None = None) -> int:
    now = now or _now()
    sent = 0
    for item in take_due(now):
        try:
            await _deliver(bot, item, now)
            sent += 1
        except Exception:
            log.exception("eslatma #%s yuborilmadi (chat=%s)", item.get("id"), item.get("chat_id"))
    return sent


async def _loop(bot) -> None:
    log.info("eslatmalar: rejalashtiruvchi ishga tushdi (%d ta faol)", len(_read()))
    while True:
        try:
            await run_due(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("eslatmalar: tekshiruv xatosi")
        await asyncio.sleep(_CHECK_SECONDS)


def start_reminders(bot) -> asyncio.Task:
    return asyncio.create_task(_loop(bot))


TOOLS = {"set_reminder": set_reminder, "list_reminders": list_reminders, "cancel_reminder": cancel_reminder}

SCHEMAS = {
    "set_reminder": {
        "name": "set_reminder",
        "description": (
            "Belgilangan vaqtda shu chatga xabar yuboradigan eslatma qo'yadi. Foydalanuvchi \"eslat\", "
            "\"...da yodimga sol\", \"har kuni/har dushanba ... yubor\" desa chaqiring. Ikki rejim: oddiy eslatma "
            "(matnning o'zi yuboriladi) va run_as_question=true — vaqti kelganda matn sizga savol sifatida "
            "beriladi va tayyor javob yuboriladi (masalan \"bugungi sotuv va tushgan to'lovlar qancha?\" har kuni "
            "18:00 da). Vaqtni joriy sana-vaqtdan hisoblab, aniq ko'rinishda bering."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {"type": "string", "description": "Birinchi yuborish vaqti: YYYY-MM-DD HH:MM (Toshkent vaqti)"},
                "text": {"type": "string", "description": "Eslatma matni; run_as_question bo'lsa — to'liq, mustaqil tushunarli savol"},
                "repeat": {"type": "string", "enum": ["", "daily", "weekdays", "weekly", "monthly"],
                           "description": "Takrorlanish; bo'sh — bir marta"},
                "run_as_question": {"type": "boolean", "description": "true — matn savol sifatida bajarilib, javobi yuboriladi"},
            },
            "required": ["when", "text"],
        },
    },
    "list_reminders": {
        "name": "list_reminders",
        "description": "Shu chatdagi faol eslatmalar va rejalashtirilgan so'rovlar ro'yxati.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "cancel_reminder": {
        "name": "cancel_reminder",
        "description": "Eslatmani bekor qiladi. Raqami noma'lum bo'lsa avval list_reminders chaqiring.",
        "input_schema": {
            "type": "object",
            "properties": {"reminder_id": {"type": "integer"}},
            "required": ["reminder_id"],
        },
    },
}
