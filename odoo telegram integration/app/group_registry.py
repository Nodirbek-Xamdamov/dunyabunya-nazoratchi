"""
Kuzatiladigan guruhlar va ularning menejerlari ro'yxati (2026-10-12) -- `monitored_groups` va
`group_managers` jadvallari ustidagi yupqa qatlam. 200+ guruhning har bir xabarida bazaga
murojaat qilmaslik uchun ikkalasi ham xotirada qisqa muddat keshlanadi.

Guruh: birinchi xabari kelganda o'zi ro'yxatga tushadi (`ensure_group`); `is_active=False`
qilingani kuzatilmaydi.

Menejer: guruh adminining unvonidan avtomatik (`source="title"`, `group_monitor.find_group_
managers` tenglashtirib turadi) yoki ichki guruhdagi `/menejer_qosh` buyrug'i bilan qo'lda
(`source="manual"`). Qo'lda yozilgan menejerning faqat username'i ma'lum -- Telegram ID'si u
guruhda birinchi marta yozganda to'ldiriladi (`is_group_manager`).
"""

import logging
import time
from dataclasses import dataclass

from sqlalchemy import delete, or_, select

from app.database import async_session_maker
from app.models import Group, Manager
from app.utils import normalize_username

logger = logging.getLogger("aiogram_bot")

_CACHE_TTL_SECONDS = 600

# group_id -> (nomi, faolmi, qachongacha amal qiladi)
_groups_cache: dict[int, tuple[str | None, bool, float]] = {}
# group_id -> (menejerlar, qachongacha amal qiladi)
_managers_cache: dict[int, tuple[tuple["ManagerInfo", ...], float]] = {}


@dataclass(frozen=True)
class ManagerInfo:
    user_id: int | None
    username: str | None
    full_name: str | None
    source: str


def clear_caches() -> None:
    _groups_cache.clear()
    _managers_cache.clear()


# ---------------------------------------------------------------------------
# Guruhlar
# ---------------------------------------------------------------------------


async def ensure_group(group_id: int, title: str | None) -> bool:
    """Guruhni ro'yxatga oladi (yo'q bo'lsa), nomi o'zgargan bo'lsa yangilaydi. Guruh
    kuzatilishi kerak bo'lsa True qaytaradi."""
    cached = _groups_cache.get(group_id)
    if cached and cached[2] > time.monotonic() and cached[0] == title:
        return cached[1]
    async with async_session_maker() as session:
        group = (await session.execute(select(Group).where(Group.group_id == group_id))).scalar_one_or_none()
        if group is None:
            group = Group(group_id=group_id, title=title, is_active=True)
            session.add(group)
            logger.info("Yangi guruh ro'yxatga olindi: '%s' (group_id=%s)", title, group_id)
        elif group.title != title:
            group.title = title
        await session.commit()
        is_active = group.is_active
    _groups_cache[group_id] = (title, is_active, time.monotonic() + _CACHE_TTL_SECONDS)
    return is_active


async def set_group_active(group_id: int, is_active: bool) -> bool:
    """Guruhni kuzatuvdan chiqaradi / qaytaradi. Guruh ro'yxatda bo'lmasa False."""
    async with async_session_maker() as session:
        group = (await session.execute(select(Group).where(Group.group_id == group_id))).scalar_one_or_none()
        if group is None:
            return False
        group.is_active = is_active
        await session.commit()
    _groups_cache.pop(group_id, None)
    return True


async def find_groups(query: str = "") -> list[Group]:
    """Guruhlarni ID'si yoki nomining bir qismi bo'yicha qidiradi (bo'sh so'rov -- hammasi)."""
    query = query.strip()
    statement = select(Group).order_by(Group.title)
    if query.lstrip("-").isdigit():
        statement = statement.where(Group.group_id == int(query))
    elif query:
        statement = statement.where(Group.title.ilike(f"%{query}%"))
    async with async_session_maker() as session:
        return list((await session.execute(statement)).scalars().all())


async def group_titles() -> dict[int, str]:
    async with async_session_maker() as session:
        rows = (await session.execute(select(Group.group_id, Group.title))).all()
    return {group_id: title or str(group_id) for group_id, title in rows}


# ---------------------------------------------------------------------------
# Menejerlar
# ---------------------------------------------------------------------------


async def list_managers(group_id: int) -> tuple[ManagerInfo, ...]:
    cached = _managers_cache.get(group_id)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    async with async_session_maker() as session:
        rows = (
            await session.execute(select(Manager).where(Manager.group_id == group_id).order_by(Manager.id))
        ).scalars().all()
    managers = tuple(ManagerInfo(row.user_id, row.username, row.full_name, row.source) for row in rows)
    _managers_cache[group_id] = (managers, time.monotonic() + _CACHE_TTL_SECONDS)
    return managers


async def all_managers() -> dict[int, list[ManagerInfo]]:
    async with async_session_maker() as session:
        rows = (await session.execute(select(Manager).order_by(Manager.group_id, Manager.id))).scalars().all()
    result: dict[int, list[ManagerInfo]] = {}
    for row in rows:
        result.setdefault(row.group_id, []).append(ManagerInfo(row.user_id, row.username, row.full_name, row.source))
    return result


async def add_manager(group_id: int, username: str, full_name: str | None = None) -> bool:
    """Menejerni qo'lda biriktiradi. Shu username allaqachon biriktirilgan bo'lsa False."""
    normalized = normalize_username(username)
    async with async_session_maker() as session:
        existing = (
            await session.execute(
                select(Manager).where(Manager.group_id == group_id, Manager.username == normalized)
            )
        ).scalars().first()
        if existing is not None:
            return False
        session.add(Manager(group_id=group_id, username=normalized, full_name=full_name, source="manual"))
        await session.commit()
    _managers_cache.pop(group_id, None)
    return True


async def remove_manager(group_id: int, username: str) -> bool:
    """Qo'lda biriktirilgan menejerni olib tashlaydi (unvon bo'yicha aniqlangani keyingi
    tekshiruvda baribir qaytadi -- uni olib tashlash uchun guruhdagi unvonini o'zgartiring)."""
    normalized = normalize_username(username)
    async with async_session_maker() as session:
        result = await session.execute(
            delete(Manager).where(Manager.group_id == group_id, Manager.username == normalized)
        )
        await session.commit()
    _managers_cache.pop(group_id, None)
    return bool(result.rowcount)


async def sync_title_managers(group_id: int, found: dict[int, tuple[str | None, str | None]]) -> None:
    """Admin unvoni bo'yicha aniqlangan menejerlarni (`found`: user_id -> (username, ism))
    jadval bilan tenglashtiradi: yangilari qo'shiladi, unvoni olib tashlanganlari o'chiriladi.
    Qo'lda yozilgan menejerlarga tegilmaydi."""
    current = {m.user_id: m for m in await list_managers(group_id) if m.source == "title"}
    wanted = {
        user_id: (normalize_username(username) if username else None, full_name)
        for user_id, (username, full_name) in found.items()
    }
    if {user_id: (m.username, m.full_name) for user_id, m in current.items()} == wanted:
        return
    async with async_session_maker() as session:
        await session.execute(delete(Manager).where(Manager.group_id == group_id, Manager.source == "title"))
        for user_id, (username, full_name) in wanted.items():
            session.add(
                Manager(group_id=group_id, user_id=user_id, username=username, full_name=full_name, source="title")
            )
        await session.commit()
    _managers_cache.pop(group_id, None)
    logger.info("Guruh menejerlari yangilandi: group_id=%s, %s ta (unvon bo'yicha)", group_id, len(wanted))


async def is_group_manager(group_id: int, user) -> bool:
    """Foydalanuvchi shu guruhga biriktirilgan menejermi (ID'si yoki username'i bo'yicha).
    Username bo'yicha topilgan menejerning Telegram ID'si va ismi shu yerda yozib qo'yiladi."""
    managers = await list_managers(group_id)
    if not managers:
        return False
    if any(manager.user_id == user.id for manager in managers):
        return True
    normalized = normalize_username(user.username) if user.username else None
    if not normalized or not any(manager.username == normalized for manager in managers):
        return False
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(Manager).where(
                    Manager.group_id == group_id,
                    Manager.username == normalized,
                    or_(Manager.user_id.is_(None), Manager.user_id != user.id),
                )
            )
        ).scalars().all()
        for row in rows:
            row.user_id = user.id
            row.full_name = row.full_name or user.full_name
        await session.commit()
    _managers_cache.pop(group_id, None)
    return True
