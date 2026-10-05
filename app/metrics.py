"""Kunlik hisoblagichlar — tizim salomatligi hisoboti (P3-6) va /status buyrug'i uchun.

Har hodisa (savol, tool chaqiruvi, xato, yaratilgan vazifa...) shu kungi (Toshkent
sanasi) hisoblagichga qo'shiladi. Xotirada yuritiladi va `storage/metrics.json` ga
vaqti-vaqti bilan (monitoring tsikli va to'xtash paytida) yoziladi — bot qayta ishga
tushsa ham haftalik raqamlar yo'qolmaydi. Aniq hisob-kitob emas, umumiy manzara uchun.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings

log = logging.getLogger(__name__)

_PATH = Path(settings.STORAGE_DIR) / "metrics.json"
_TZ = ZoneInfo("Asia/Tashkent")
_KEEP_DAYS = 60

_days: dict[str, dict[str, int]] = {}
_loaded = False
_dirty = False


def _today() -> str:
    return datetime.now(_TZ).date().isoformat()


def _ensure_loaded() -> None:
    global _days, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if _PATH.exists():
            raw = json.loads(_PATH.read_text(encoding="utf-8"))
            _days = {str(day): {str(k): int(v) for k, v in counters.items()}
                     for day, counters in raw.items() if isinstance(counters, dict)}
    except Exception:  # noqa: BLE001 — buzuq fayl hisoblagichlarni to'xtatmasligi kerak
        log.warning("metrics: %s o'qib bo'lmadi — hisoblagichlar noldan boshlanadi", _PATH)
        _days = {}


def inc(name: str, amount: int = 1) -> None:
    """Shu kungi `name` hisoblagichini oshiradi (istalgan oqimdan chaqirish mumkin)."""
    global _dirty
    if not amount:
        return
    _ensure_loaded()
    bucket = _days.setdefault(_today(), {})
    bucket[name] = bucket.get(name, 0) + int(amount)
    _dirty = True


def add_usage(usage) -> None:
    """Claude javobidagi token sarfini qo'shadi (`usage` yo'q bo'lsa — hech narsa)."""
    if usage is None:
        return
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    written = getattr(usage, "cache_creation_input_tokens", 0) or 0
    fresh = getattr(usage, "input_tokens", 0) or 0
    try:
        inc("llm_requests")
        inc("tokens_in", int(fresh) + int(read) + int(written))
        inc("tokens_cached", int(read))
        inc("tokens_out", int(getattr(usage, "output_tokens", 0) or 0))
    except (TypeError, ValueError):
        pass


def totals(days: int = 1) -> dict[str, int]:
    """Oxirgi `days` kun (bugun ham kiradi) bo'yicha yig'indilar."""
    _ensure_loaded()
    today = datetime.now(_TZ).date()
    wanted = {(today - timedelta(days=i)).isoformat() for i in range(max(1, days))}
    result: dict[str, int] = {}
    for day, counters in _days.items():
        if day in wanted:
            for key, value in counters.items():
                result[key] = result.get(key, 0) + value
    return result


def flush() -> None:
    """O'zgargan bo'lsa diskka yozadi (atomik) va eski kunlarni tashlab yuboradi."""
    global _dirty
    if not _dirty:
        return
    cutoff = (datetime.now(_TZ).date() - timedelta(days=_KEEP_DAYS)).isoformat()
    for day in [d for d in _days if d < cutoff]:
        _days.pop(day, None)
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_days, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(_PATH)
        _dirty = False
    except OSError as e:
        log.warning("metrics: %s ga yozib bo'lmadi: %s", _PATH, e)


class ErrorCounter(logging.Handler):
    """ERROR darajasidagi log yozuvlarini sanaydi ("errors" hisoblagichi)."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        if record.name != __name__:
            inc("errors")
