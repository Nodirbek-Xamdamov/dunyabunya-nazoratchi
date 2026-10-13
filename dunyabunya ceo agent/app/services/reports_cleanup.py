"""Hisobotlar papkasini (storage/reports) muddati o'tgan fayllardan tozalash.

P0-2: hisobot havolalari abadiy ishlab turmasligi kerak — REPORT_LINK_TTL_HOURS
(sukut 48 soat) dan eski fayllar avtomatik o'chiriladi, shundan keyin o'sha
havola /reports/... 404 qaytaradi. Bu Excel/PDF/HTML — barcha turdagi
hisobotlarga baravar qo'llanadi (hammasi bitta REPORTS_DIR'da saqlanadi).
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

REPORTS_DIR = Path(settings.STORAGE_DIR) / "reports"
_CHECK_INTERVAL_SECONDS = 3600  # har soatda bir marta tekshiradi


def cleanup_expired_reports(*, ttl_hours: int | None = None) -> int:
    """REPORTS_DIR'dagi TTL'dan eski fayllarni o'chiradi. O'chirilgan fayllar
    sonini qaytaradi. Fayl ustida boshqa jarayon ishlayotgan bo'lsa (masalan
    hozir yozilyapti) — shu faylni o'tkazib yuboradi, butun tozalashni
    to'xtatmaydi."""
    ttl = ttl_hours if ttl_hours is not None else settings.REPORT_LINK_TTL_HOURS
    if not REPORTS_DIR.exists():
        return 0
    cutoff = time.time() - ttl * 3600
    removed = 0
    for path in REPORTS_DIR.iterdir():
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as e:
            log.warning("reports_cleanup: %s o'chirib bo'lmadi: %s", path.name, e)
    return removed


async def _cleanup_loop() -> None:
    log.info("reports_cleanup: rejalashtiruvchi ishga tushdi (TTL=%d soat, har %ds tekshiradi)",
              settings.REPORT_LINK_TTL_HOURS, _CHECK_INTERVAL_SECONDS)
    while True:
        try:
            removed = await asyncio.to_thread(cleanup_expired_reports)
            if removed:
                log.info("reports_cleanup: %d ta muddati o'tgan hisobot fayli o'chirildi", removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reports_cleanup: tozalashda xato")
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)


def start_reports_cleanup_scheduler() -> asyncio.Task:
    """main.py lifespan ichida chaqiriladi — fon tozalash vazifasini boshlaydi."""
    return asyncio.create_task(_cleanup_loop())
