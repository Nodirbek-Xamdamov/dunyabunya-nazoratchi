"""Yordamchining uzoq muddatli xotirasi.

1. Qaydlar — foydalanuvchi "eslab qol", "bundan keyin doim ..." degan fakt va afzalliklar.
   `storage/agent_memory.json` da saqlanadi va har so'rovda tizim ko'rsatmasiga qo'shiladi,
   shuning uchun bot qayta ishga tushgandan keyin ham unutilmaydi.
2. Guruh tarixi — CEO'ning guruhga yozganlari (bugungisi bazada, oldingi kunlar
   `storage/memory/CLAUDE.md` dagi kunlik xulosalarda) — yordamchi so'ralganda o'qiydi.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.db import repo
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

_PATH = Path(settings.STORAGE_DIR) / "agent_memory.json"
_MEMORY_DIR = Path(settings.STORAGE_DIR) / "memory"
_TZ = ZoneInfo("Asia/Tashkent")
MAX_NOTES = 40
MAX_NOTE_CHARS = 300
_lock = threading.Lock()


def _read() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")) if _PATH.exists() else {}
    except Exception:  # noqa: BLE001
        log.warning("agent_memory: %s o'qib bo'lmadi — bo'sh deb olinadi", _PATH)
        return {}


def _write(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_PATH)


def notes(owner: str) -> list[dict]:
    return list(_read().get(owner) or [])


def remember(owner: str, text: str) -> dict:
    text = " ".join(str(text or "").split())[:MAX_NOTE_CHARS]
    if not text:
        return {"error": "Eslab qolinadigan matn bo'sh."}
    with _lock:
        data = _read()
        items = data.setdefault(owner, [])
        for item in items:
            if item["text"].casefold() == text.casefold():
                return {"saqlandi": True, "id": item["id"], "izoh": "Bu qayd avvaldan bor."}
        if len(items) >= MAX_NOTES:
            return {"error": f"Xotira to'la ({MAX_NOTES} ta qayd). Avval keraksizini forget_note bilan o'chiring."}
        note = {"id": max((i["id"] for i in items), default=0) + 1, "text": text,
                "sana": datetime.now(_TZ).strftime("%Y-%m-%d")}
        items.append(note)
        _write(data)
    return {"saqlandi": True, "id": note["id"]}


def forget(owner: str, note_id: int) -> dict:
    with _lock:
        data = _read()
        items = data.get(owner) or []
        kept = [i for i in items if i["id"] != int(note_id)]
        if len(kept) == len(items):
            return {"error": f"#{note_id} raqamli qayd topilmadi."}
        data[owner] = kept
        _write(data)
    return {"ochirildi": True, "id": int(note_id)}


def render(owner: str) -> str:
    """Tizim ko'rsatmasiga qo'shiladigan qaydlar ro'yxati (bo'sh bo'lsa "")."""
    items = notes(owner)
    return "\n".join(f"- (#{i['id']}) {i['text']}" for i in items)


# ─── Guruh tarixi ───────────────────────────────────────────────────────────

def _daily_entries() -> list[tuple[str, str]]:
    """Kunlik xulosalar: [(sana "YYYY-MM-DD", matn)] — fayldagi tartibda (eskisidan yangisiga)."""
    path = _MEMORY_DIR / "CLAUDE.md"
    if not path.exists():
        return []
    entries = []
    for block in path.read_text(encoding="utf-8").split("\n---\n"):
        block = block.strip()
        if not block.startswith("## "):
            continue
        header, _, body = block.partition("\n")
        entries.append((header[3:].strip()[:10], body.strip()))
    return entries


async def group_history(days: int = 7, query: str = "") -> dict:
    days = max(1, min(int(days or 7), 90))
    needle = (query or "").strip().casefold()
    async with SessionLocal() as session:
        fresh = await repo.get_all_group_messages(session, settings.TELEGRAM_GROUP_ID)
    lines = []
    for m in fresh:
        created = m.created_at.astimezone(_TZ).strftime("%m-%d %H:%M") if m.created_at else "?"
        text = (m.text or "")[:400]
        if needle and needle not in text.casefold():
            continue
        lines.append(f"[{created}] {m.from_user_name}: {text}")
    since = (datetime.now(_TZ).date() - timedelta(days=days)).isoformat()
    digests = []
    for day, body in _daily_entries():
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        if day >= since and (not needle or needle in body.casefold()):
            digests.append({"sana": day, "xulosa": body[:1500]})
    return {
        "hali_xulosalanmagan_xabarlar": lines[-120:],
        "kunlik_xulosalar": digests[-days:],
        "izoh": "Bot guruhda faqat rahbar yozgan xabarlarni saqlaydi; xodimlarning javoblari bu yerda yo'q.",
    }
