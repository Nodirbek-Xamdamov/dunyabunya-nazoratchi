"""Fayl-asosli hodimlar katalogi — Postgres o'rniga JSON faylda saqlanadi.

~60-100 ta hodim uchun relatsion jadval keraksiz og'irlik: butun katalog
xotirada saqlanadi, ism bo'yicha qidiruv oddiy Python substring solishtirish
bilan bajariladi (avvalgi SQL ILIKE so'roviga qaraganda tezroq — tarmoq/DB
round-trip yo'q, va ILIKE '%...%' baribir indeksdan foydalana olmasdi).

Yozishlar (ro'yxatdan o'tish/bog'lanishni bekor qilish/Odoo'dan sinxronlash)
`settings.STORAGE_DIR/employees.json`ga ATOMIK saqlanadi (tmp fayl + os.replace)
— shu papka docker-compose'da volume sifatida bog'langan, konteyner qayta
qurilganda ham ma'lumot yo'qolmaydi. Bitta asyncio.Lock barcha yozishlarni
tartiblaydi (jarayon bitta, lekin update'lar parallel task sifatida ishlaydi).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.name_utils import normalize_name_for_search

log = logging.getLogger(__name__)

_PATH = Path(settings.STORAGE_DIR) / "employees.json"
_lock = asyncio.Lock()


@dataclass
class Employee:
    id: int
    telegram_user_id: int | None = None
    telegram_username: str | None = None
    full_name: str = ""
    odoo_user_id: int | None = None
    odoo_user_name: str | None = None
    is_active: bool = True
    registered_at: str | None = None


_employees: list[Employee] = []
_by_telegram_id: dict[int, Employee] = {}
_next_id = 1
_loaded = False


def _reindex() -> None:
    global _by_telegram_id
    _by_telegram_id = {
        e.telegram_user_id: e for e in _employees if e.telegram_user_id and e.is_active
    }


def _load() -> None:
    global _employees, _next_id, _loaded
    if _PATH.exists():
        raw = json.loads(_PATH.read_text(encoding="utf-8"))
    else:
        raw = []
    _employees = [Employee(**row) for row in raw]
    _next_id = max((e.id for e in _employees), default=0) + 1
    _reindex()
    _loaded = True
    log.info("employee_store: %d ta hodim yuklandi (%s)", len(_employees), _PATH)


def _ensure_loaded() -> None:
    if not _loaded:
        _load()


def _save() -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps([asdict(e) for e in _employees], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(_PATH)
    _reindex()


# ─── O'qish (yozuvsiz, lock kerak emas — GIL tufayli xavfsiz) ──────────────


async def get_employee_by_telegram_id(telegram_user_id: int) -> Employee | None:
    _ensure_loaded()
    return _by_telegram_id.get(telegram_user_id)


async def get_employee_by_odoo_user_id(odoo_user_id: int) -> Employee | None:
    """Rasmiy Odoo hisobiga (res.users) BOG'LANGAN (ya'ni botda ro'yxatdan
    o'tgan) hodimni topadi — DM yuborish uchun."""
    _ensure_loaded()
    for e in _employees:
        if e.odoo_user_id == odoo_user_id and e.telegram_user_id and e.is_active:
            return e
    return None


def _name_pattern(name: str) -> str | None:
    q = normalize_name_for_search(name).strip().casefold()
    if len(q) < 2:
        return None
    return q


def _matches(emp: Employee, pattern: str) -> bool:
    return any(
        pattern in (target or "").casefold()
        for target in (emp.odoo_user_name, emp.full_name)
    )


async def get_employee_by_name(name: str) -> Employee | None:
    """Bitta aniq moslikni qaytaradi. Bir nechta moslik topilsa None (chaqiruvchi
    tanlash tugmalarini ko'rsatishi kerak) — avvalgi xulq-atvor bilan bir xil."""
    matches = await get_employees_by_name(name)
    return matches[0] if len(matches) == 1 else None


async def get_employees_by_name(name: str) -> list[Employee]:
    _ensure_loaded()
    pattern = _name_pattern(name)
    if pattern is None:
        return []
    return [e for e in _employees if e.is_active and _matches(e, pattern)]


async def find_active_employee_by_name(name: str) -> Employee | None:
    """Ismi bo'yicha BOTDA RO'YXATDAN O'TGAN (telegram_user_id bor) hodimni
    topadi — Odoo res.users/hr.employee bog'lanishi borligidan qat'iy nazar.

    Vazifa bildirishnomasini (DM/ovozli xabar) Odoo login holatidan mustaqil
    yetkazish uchun ishlatiladi: CEO ismni to'g'ri aytgan va xodim /start
    bosgan bo'lsa, Odoo'da rasmiy hisobi bo'lmasa ham xabar borishi kerak.
    Bir nechta moslik topilsa (noaniqlik) — xato odamga yubormaslik uchun None."""
    _ensure_loaded()
    pattern = _name_pattern(name)
    if pattern is None:
        return None
    matches = [e for e in _employees if e.is_active and e.telegram_user_id and _matches(e, pattern)]
    return matches[0] if len(matches) == 1 else None


# ─── Yozish (asyncio.Lock bilan himoyalangan) ──────────────────────────────


async def upsert_employee(
    *,
    telegram_user_id: int,
    telegram_username: str | None,
    full_name: str,
    odoo_user_id: int | None = None,
    odoo_user_name: str | None = None,
) -> Employee:
    global _next_id
    async with _lock:
        _ensure_loaded()
        emp = next((e for e in _employees if e.telegram_user_id == telegram_user_id), None)
        if emp is None:
            emp = Employee(
                id=_next_id,
                telegram_user_id=telegram_user_id,
                full_name=full_name,
                registered_at=datetime.now(timezone.utc).isoformat(),
            )
            _next_id += 1
            _employees.append(emp)
        emp.telegram_username = telegram_username
        emp.full_name = full_name
        if odoo_user_id is not None:
            emp.odoo_user_id = odoo_user_id
        if odoo_user_name is not None:
            emp.odoo_user_name = odoo_user_name
        emp.is_active = True
        _save()
        return emp


async def unlink_employee(telegram_user_id: int) -> Employee | None:
    """Hodimning Telegram bog'lanishini bekor qiladi (Odoo katalog yozuvi
    o'chirilmaydi — faqat telegram_user_id/username tozalanadi, is_active=False)."""
    async with _lock:
        _ensure_loaded()
        emp = next((e for e in _employees if e.telegram_user_id == telegram_user_id), None)
        if emp is None:
            return None
        emp.telegram_user_id = None
        emp.telegram_username = None
        emp.is_active = False
        _save()
        return emp


async def seed_employees_from_odoo(users: list[dict]) -> int:
    """Odoo'dagi res.users ro'yxatini local katalogga sinkronlaydi. Faqat
    odoo_user_id bo'yicha unique qidiradi — takroriy yozuv qo'shmaydi."""
    global _next_id
    async with _lock:
        _ensure_loaded()
        existing_odoo_ids = {e.odoo_user_id for e in _employees if e.odoo_user_id}
        added = 0
        for user in users:
            odoo_id = user["id"]
            if odoo_id in existing_odoo_ids:
                continue
            _employees.append(
                Employee(
                    id=_next_id,
                    full_name=user["name"],
                    odoo_user_id=odoo_id,
                    odoo_user_name=user["name"],
                    is_active=True,
                )
            )
            _next_id += 1
            existing_odoo_ids.add(odoo_id)
            added += 1
        if added:
            _save()
        return added
