"""CEO'ning raqamli Telegram user_id'sini avtomatik aniqlab saqlash.

`settings.CEO_TELEGRAM_USER_ID` .env'da 0 (sozlanmagan) bo'lishi mumkin —
CEO faqat username (`CEO_TELEGRAM_USERNAME`) orqali tanilgan. Bu yetarli
INKOMING xabarni tekshirish uchun (`_is_ceo_message`/`_is_ceo`), lekin
botning O'ZI CEO'ga FAOL (proaktiv) xabar yuborishi kerak bo'lganda
(masalan P1-6: muddati o'tgan vazifalar eslatmasi) raqamli chat_id shart.

Shuning uchun: CEO username bo'yicha tanilgan HAR safar (u yozgan har
qanday xabarda) uning haqiqiy user_id'si shu yerga eslab qolinadi —
`employee_store.py` bilan bir xil fayl-asosli, atomik yozuvli naqsh."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

_PATH = Path(settings.STORAGE_DIR) / "ceo_state.json"
_lock = asyncio.Lock()

_cached_id: int | None = None
_loaded = False


def _load() -> None:
    global _cached_id, _loaded
    if _PATH.exists():
        try:
            data = json.loads(_PATH.read_text(encoding="utf-8"))
            _cached_id = data.get("telegram_user_id")
        except Exception:
            log.warning("ceo_state: %s o'qib bo'lmadi", _PATH)
            _cached_id = None
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        _load()


def _save() -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"telegram_user_id": _cached_id}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_PATH)


async def get_ceo_telegram_user_id() -> int | None:
    """Ma'lum bo'lgan CEO chat_id — avval aniqlangan/saqlangan qiymat, aks
    holda .env'dagi CEO_TELEGRAM_USER_ID (agar sozlangan bo'lsa)."""
    _ensure_loaded()
    if _cached_id:
        return _cached_id
    return settings.CEO_TELEGRAM_USER_ID or None


async def remember_ceo_telegram_user_id(user_id: int) -> None:
    """CEO username bo'yicha tanilgan xabar kelganda chaqiriladi — raqamli
    ID'ni birinchi marta yoki o'zgargan bo'lsa yangilaydi."""
    global _cached_id
    _ensure_loaded()
    if _cached_id == user_id:
        return
    async with _lock:
        _cached_id = user_id
        _save()
    log.info("ceo_state: CEO telegram_user_id aniqlandi/yangilandi: %s", user_id)
