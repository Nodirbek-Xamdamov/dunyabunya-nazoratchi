"""Kunlik/haftalik "xotira" — guruh xabarlarini OpenAI bilan xulosalab, DB'da
abadiy saqlash o'rniga ikki qatlamli FAYL-asosli xotiraga yozadi:

1. KUNLIK (har kuni bir marta, Asia/Tashkent 00:10): o'sha kungi guruh
   xabarlari (GroupMessage jadvali) OpenAI bilan qisqacha o'zbekcha xulosaga
   aylantiriladi, `storage/memory/CLAUDE.md`ga sana bilan qo'shiladi (append).
   Shundan keyin o'sha kungi xom xabarlar bazadan o'chiriladi — guruh xabarlar
   jadvali cheksiz o'smasligi, faqat qisqa xulosa saqlanib qolishi uchun.
2. HAFTALIK (har hafta dushanba 00:20): CLAUDE.md'dagi so'nggi 7 ta kunlik
   yozuv yana bir marta OpenAI bilan birlashtirilib, `storage/memory/
   WEEKLY_MEMORY.md`ga qo'shiladi — bu "2-darajali xotira": kunlik tafsilotlar
   yo'qolmaydi (CLAUDE.md'da qolaveradi), lekin uzoq muddatli umumiy manzara
   uchun qisqa haftalik xulosa ham to'planib boradi.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.models import GroupMessage
from app.db.repo import delete_group_messages, get_all_group_messages
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

_openai_client = None


def _get_openai_client():
    """Lazy singleton OpenAI async client (testlarda monkeypatch qilish oson)."""
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI

        _openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=25, max_retries=0)
    return _openai_client


MEMORY_DIR = Path(settings.STORAGE_DIR) / "memory"
DAILY_MEMORY_PATH = MEMORY_DIR / "CLAUDE.md"
WEEKLY_MEMORY_PATH = MEMORY_DIR / "WEEKLY_MEMORY.md"

_ENTRY_SEP = "\n---\n"

_TZ = ZoneInfo("Asia/Tashkent")
_DAILY_TIME = time(hour=0, minute=10)
_WEEKLY_TIME = time(hour=0, minute=20)
_WEEKLY_WEEKDAY = 0  # dushanba

_DAILY_SYSTEM = """Siz kompaniyaning ichki Telegram guruh suhbatini kuzatib boruvchi yordamchisiz.
Quyida bir kunlik xabarlar ro'yxati berilgan (vaqt, kim, nima yozgan). Shu kun davomida nima
bo'lganini, muhim qarorlar, muammolar va topshiriqlarni QISQA (5-10 gap) o'zbek tilida
xulosalab bering. Xabarlarni birma-bir qayta sanab o'tirmang — faqat MUHIM narsalarni
ajratib bering. Agar mazmunli hech narsa bo'lmasa (faqat salomlashish va h.k.), shunday deb
qisqa yozing."""

_WEEKLY_SYSTEM = """Siz kompaniyaning kunlik xulosalarini haftalik xulosaga birlashtiruvchi
yordamchisiz. Quyida oxirgi bir haftalik kunlik xulosalar berilgan. Shu hafta davomida
umumiy qanday tendensiya, takrorlanuvchi muammolar va eng muhim voqealar bo'lganini QISQA
(6-12 gap) o'zbek tilida umumlashtiring."""


async def _openai_text(system: str, content: str) -> str:
    try:
        response = await asyncio.wait_for(
            _get_openai_client().chat.completions.create(
                model=settings.OPENAI_CHAT_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                max_completion_tokens=1024,
            ),
            timeout=25,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception:
        log.exception("memory_digest: OpenAI so'rovi muvaffaqiyatsiz")
        return ""


def _format_messages(messages: list[GroupMessage]) -> str:
    lines = []
    for m in messages:
        created = m.created_at
        if created is not None and created.tzinfo is not None:
            created = created.astimezone(_TZ)  # bazada UTC — xulosada Toshkent vaqti
        ts = created.strftime("%H:%M") if created else "?"
        lines.append(f"[{ts}] {m.from_user_name}: {m.text}")
    return "\n".join(lines)


def _append(path: Path, entry: str) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)


async def run_daily_digest(chat_id: int) -> str | None:
    """O'sha kungi guruh xabarlarini xulosalab CLAUDE.md'ga yozadi, so'ng xom
    xabarlarni bazadan o'chiradi. Xabar bo'lmasa hech narsa qilmaydi (None)."""
    async with SessionLocal() as session:
        messages = await get_all_group_messages(session, chat_id)
    if not messages:
        log.info("run_daily_digest: chat=%s uchun xabar yo'q, o'tkazib yuborildi", chat_id)
        return None

    transcript = _format_messages(messages)
    summary = await _openai_text(_DAILY_SYSTEM, transcript)
    if not summary:
        # Xulosa chiqmadi, xom xabarlar esa pastda o'chiriladi — kun mazmuni yo'qolmasligi
        # uchun ularning o'zi yozib qo'yiladi (ilgari faqat "tayyorlab bo'lmadi" qolardi)
        summary = "(xulosa tayyorlab bo'lmadi — xom xabarlar:)\n" + transcript[:6000]

    # 00:10 da ishga tushadi — yozuv O'TGAN kunga tegishli
    day = (datetime.now(_TZ) - timedelta(hours=1)).date().isoformat()
    _append(DAILY_MEMORY_PATH, f"## {day}\n\n{summary}\n{_ENTRY_SEP}")

    # Faqat xulosalangan xabarlar o'chiriladi: xulosa tayyorlanayotgan soniyalarda kelgan
    # yangi xabarlar keyingi kunga qoladi (ilgari ular xulosasiz o'chib ketardi)
    ids = [i for i in (getattr(m, "id", None) for m in messages) if i is not None]
    async with SessionLocal() as session:
        deleted = await delete_group_messages(session, chat_id, up_to_id=max(ids) if ids else None)
    log.info("run_daily_digest: chat=%s — %d xabar xulosalandi, %d ta o'chirildi",
              chat_id, len(messages), deleted)
    return summary


def _last_n_daily_entries(n: int) -> list[str]:
    if not DAILY_MEMORY_PATH.exists():
        return []
    raw = DAILY_MEMORY_PATH.read_text(encoding="utf-8")
    entries = [e.strip() for e in raw.split(_ENTRY_SEP) if e.strip()]
    return entries[-n:]


async def run_weekly_digest() -> str | None:
    """CLAUDE.md'dagi so'nggi 7 ta kunlik yozuvni birlashtirib, haftalik
    xulosani WEEKLY_MEMORY.md'ga qo'shadi (2-darajali xotira). Kunlik yozuv
    bo'lmasa hech narsa qilmaydi (None) — CLAUDE.md'dagi yozuvlar o'chirilmaydi."""
    entries = _last_n_daily_entries(7)
    if not entries:
        log.info("run_weekly_digest: kunlik yozuv yo'q, o'tkazib yuborildi")
        return None

    summary = await _openai_text(_WEEKLY_SYSTEM, "\n\n".join(entries)) or "(xulosa tayyorlab bo'lmadi)"

    label = date.today().isoformat()
    _append(WEEKLY_MEMORY_PATH, f"## {label} holatiga haftalik xulosa\n\n{summary}\n{_ENTRY_SEP}")

    log.info("run_weekly_digest: %d ta kunlik yozuv asosida haftalik xulosa yozildi", len(entries))
    return summary


# ─── Rejalashtiruvchi (asyncio, tashqi kutubxonasiz) ───────────────────────

def _next_run(target_time: time, weekday: int | None = None) -> datetime:
    now = datetime.now(_TZ)
    target = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
    if weekday is not None:
        days_ahead = (weekday - now.weekday()) % 7
        target += timedelta(days=days_ahead)
        if target <= now:
            target += timedelta(days=7)
    elif target <= now:
        target += timedelta(days=1)
    return target


async def _daily_loop(chat_id: int) -> None:
    log.info("memory_digest: kunlik xulosa rejalashtiruvchisi ishga tushdi (har kuni %s, Asia/Tashkent)", _DAILY_TIME)
    while True:
        target = _next_run(_DAILY_TIME)
        try:
            await asyncio.sleep(max(0.0, (target - datetime.now(_TZ)).total_seconds()))
            await run_daily_digest(chat_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("memory_digest: kunlik xulosa xatosi")
        await asyncio.sleep(60)  # ikki marta ketma-ket ishga tushmasligi uchun


async def _weekly_loop() -> None:
    log.info("memory_digest: haftalik xulosa rejalashtiruvchisi ishga tushdi (har dushanba %s, Asia/Tashkent)", _WEEKLY_TIME)
    while True:
        target = _next_run(_WEEKLY_TIME, weekday=_WEEKLY_WEEKDAY)
        try:
            await asyncio.sleep(max(0.0, (target - datetime.now(_TZ)).total_seconds()))
            await run_weekly_digest()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("memory_digest: haftalik xulosa xatosi")
        await asyncio.sleep(60)


def start_memory_scheduler(chat_id: int) -> list[asyncio.Task]:
    """main.py lifespan ichida chaqiriladi — 2 ta fon vazifasini boshlaydi."""
    return [
        asyncio.create_task(_daily_loop(chat_id)),
        asyncio.create_task(_weekly_loop()),
    ]
