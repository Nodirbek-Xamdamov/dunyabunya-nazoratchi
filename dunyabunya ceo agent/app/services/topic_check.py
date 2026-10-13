"""Telegram topiklari to'g'ri Odoo loyihalariga ulanganini tekshirish.

`topics` jadvali har bo'lim topikini Odoo loyihasining RAQAMIGA bog'laydi. Odoo
instansiyasi almashganda (bu uch marta bo'lgan) raqamlar o'zgaradi va jadval qo'lda
yangilanadi — 2026-10-05 da aniqlangan: "Sotuv" topiki "Moliya" loyihasiga, "Moliya"
topiki "Sotuv" loyihasiga ulanib qolgan, vazifalar boshqa bo'limga yozilayotgan edi.

Bot har ishga tushganda shu moslikni tekshiradi va nomuvofiqlikni ERROR bilan logga
yozadi (ALERT_CHAT_ID sozlangan bo'lsa — Telegram'ga ham). Tuzatish:
`python scripts/check_topics.py --fix`.
"""

from __future__ import annotations

import asyncio
import logging

from app.db import repo
from app.db.session import SessionLocal
from app.services.odoo import OdooClient

log = logging.getLogger(__name__)

_odoo = OdooClient()

# Odoo'ga hech qachon vazifa yozilmaydigan topiklar (General) — loyihasi tekshirilmaydi
IGNORED_TOPIC_NAMES = {"general"}


def _same(a: str, b: str) -> bool:
    return (a or "").strip().casefold() == (b or "").strip().casefold()


async def find_topic_mismatches() -> list[dict]:
    """Noto'g'ri ulangan topiklar ro'yxati. Har biri: {topic_id, name, odoo_project_id,
    odoo_project_name (None — loyiha yo'q), correct_project_id (None — shu nomli loyiha yo'q)}."""
    async with SessionLocal() as session:
        topics = [t for t in await repo.list_topics(session) if t.name.casefold() not in IGNORED_TOPIC_NAMES]
    if not topics:
        return []

    projects = await _odoo.execute_kw(
        "project.project", "search_read",
        [["|", ["id", "in", [t.odoo_project_id for t in topics]], ["name", "in", [t.name for t in topics]]]],
        {"fields": ["id", "name"], "context": {"active_test": False}},
    )
    by_id = {p["id"]: p["name"] for p in projects}
    mismatches = []
    for topic in topics:
        linked_name = by_id.get(topic.odoo_project_id)
        if linked_name is not None and _same(linked_name, topic.name):
            continue
        candidates = [p["id"] for p in projects if _same(p["name"], topic.name)]
        mismatches.append({
            "topic_id": topic.telegram_topic_id,
            "name": topic.name,
            "odoo_project_id": topic.odoo_project_id,
            "odoo_project_name": linked_name,
            # Bir xil nomli bir nechta loyiha bo'lsa — qaysi biri to'g'riligini taxmin qilmaymiz
            "correct_project_id": candidates[0] if len(candidates) == 1 else None,
            "candidates": len(candidates),  # 0 — Odoo'da shu nomli loyiha umuman yo'q
        })
    return mismatches


async def create_project(name: str) -> int:
    """Odoo'da bo'lim loyihasini yaratadi (yangi Odoo bazasiga o'tilganda) va raqamini qaytaradi."""
    return await _odoo.execute_kw("project.project", "create", [{"name": name}])


async def verify_topic_projects(delay_seconds: float = 5.0) -> None:
    """Ishga tushgandan keyin bir marta tekshiradi; xato bo'lsa botni to'xtatmaydi."""
    try:
        await asyncio.sleep(delay_seconds)
        mismatches = await find_topic_mismatches()
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("topic_check: topiklarni tekshirib bo'lmadi (%s)", e)
        return
    if not mismatches:
        log.info("topic_check: barcha topiklar o'z nomidagi Odoo loyihasiga ulangan")
        return
    for m in mismatches:
        linked = (f"'{m['odoo_project_name']}' loyihasiga (#{m['odoo_project_id']})"
                  if m["odoo_project_name"] else f"mavjud bo'lmagan #{m['odoo_project_id']} loyihaga")
        log.error("topic_check: '%s' topiki %s ulangan — shu topikdagi vazifalar NOTO'G'RI joyga "
                  "yoziladi. Tuzatish: python scripts/check_topics.py --fix", m["name"], linked)
