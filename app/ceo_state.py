"""CEO(lar)ning raqamli Telegram user_id'sini avtomatik aniqlab saqlash.

`settings.CEO_TELEGRAM_USER_ID` .env'da 0 (sozlanmagan) bo'lishi mumkin —
CEO faqat username (`CEO_TELEGRAM_USERNAME`, vergul bilan bir nechta) orqali
tanilgan. Bu yetarli INKOMING xabarni tekshirish uchun (`is_ceo`), lekin
botning O'ZI CEO'ga FAOL (proaktiv) xabar yuborishi kerak bo'lganda
(masalan P1-6: muddati o'tgan vazifalar eslatmasi) raqamli chat_id shart.

Shuning uchun: CEO username bo'yicha tanilgan HAR safar (u yozgan har
qanday xabarda) uning haqiqiy user_id'si shu yerga username -> id ko'rinishida
eslab qolinadi — `employee_store.py` bilan bir xil fayl-asosli, atomik yozuvli
naqsh. .env'dan olib tashlangan CEO'ga proaktiv xabar yuborilmaydi."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

_PATH = Path(settings.STORAGE_DIR) / "ceo_state.json"
_lock = asyncio.Lock()

# username (kichik harf) -> telegram_user_id. Username'siz tanilganlar "id:<n>" kaliti bilan.
_ids: dict[str, int] = {}
_loaded = False


def _load() -> None:
    global _ids, _loaded
    _ids = {}
    if _PATH.exists():
        try:
            data = json.loads(_PATH.read_text(encoding="utf-8"))
            _ids = {str(k): int(v) for k, v in (data.get("by_username") or {}).items() if v}
            legacy = data.get("telegram_user_id")  # eski (bitta CEO) format
            if legacy and not _ids:
                _ids[f"id:{int(legacy)}"] = int(legacy)
        except Exception:
            log.warning("ceo_state: %s o'qib bo'lmadi", _PATH)
            _ids = {}
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        _load()


def _save() -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"by_username": _ids}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_PATH)


def _key(user_id: int, username: str | None) -> str:
    return username.lstrip("@").casefold() if username else f"id:{user_id}"


async def get_ceo_telegram_user_ids() -> list[int]:
    """Proaktiv xabar yuboriladigan barcha ma'lum CEO chat_id'lari: hozir .env'da
    ro'yxatda turgan username'lar bo'yicha eslab qolinganlar + CEO_TELEGRAM_USER_ID."""
    _ensure_loaded()
    allowed = settings.ceo_usernames
    result: list[int] = []
    for key, uid in _ids.items():
        if (key.startswith("id:") or key in allowed) and uid not in result:
            result.append(uid)
    if settings.CEO_TELEGRAM_USER_ID and settings.CEO_TELEGRAM_USER_ID not in result:
        result.append(settings.CEO_TELEGRAM_USER_ID)
    return result


async def get_ceo_telegram_user_id() -> int | None:
    """Eski API (bitta CEO) — birinchi ma'lum CEO chat_id yoki None."""
    ids = await get_ceo_telegram_user_ids()
    return ids[0] if ids else None


async def remember_ceo_telegram_user_id(user_id: int, username: str | None = None) -> None:
    """CEO username bo'yicha tanilgan xabar kelganda chaqiriladi — raqamli
    ID'ni birinchi marta yoki o'zgargan bo'lsa yangilaydi."""
    _ensure_loaded()
    key = _key(user_id, username)
    if _ids.get(key) == user_id:
        return
    async with _lock:
        _ids[key] = user_id
        _save()
    log.info("ceo_state: CEO telegram_user_id aniqlandi/yangilandi: %s -> %s", key, user_id)


def is_ceo(user_id: int, username: str | None) -> bool:
    """Foydalanuvchi CEO'mi (ID yoki .env'dagi username'lardan biri bo'yicha)?
    Username bo'yicha tanilsa — raqamli ID fonda eslab qolinadi (sinxron chaqiruvchilar uchun)."""
    if settings.CEO_TELEGRAM_USER_ID and user_id == settings.CEO_TELEGRAM_USER_ID:
        return True
    if username and username.lstrip("@").casefold() in settings.ceo_usernames:
        asyncio.create_task(_capture(user_id, username))
        return True
    return False


async def _capture(user_id: int, username: str) -> None:
    try:
        await remember_ceo_telegram_user_id(user_id, username)
    except Exception:
        log.warning("CEO telegram_user_id'ni saqlashda xato", exc_info=True)
