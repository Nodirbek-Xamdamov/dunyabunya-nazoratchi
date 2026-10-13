"""Kunlik rejalashtirilgan ishlarning oxirgi bajarilgan sanasi (`storage/scheduler_state.json`).

Rejalashtiruvchilar xotirada "soat 09:00 gacha kut" deb turadi — bot aynan o'sha paytda
o'chiq yoki qayta ishga tushayotgan bo'lsa, o'sha kungi ish (eslatma, brifing) butunlay
o'tkazib yuborilardi. Sana diskda saqlangani uchun bot qayta turgach "bugun hali
bajarilmagan" ekanini biladi va ishni kechikib bo'lsa ham bajaradi.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

_PATH = Path(settings.STORAGE_DIR) / "scheduler_state.json"


def _read() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8")) if _PATH.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def last_run(job: str) -> str | None:
    """`job` oxirgi marta qaysi sanada (YYYY-MM-DD) bajarilgan."""
    value = _read().get(job)
    return str(value) if value else None


def mark_run(job: str, day: str) -> None:
    state = _read()
    state[job] = day
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_PATH)
    except OSError as e:
        log.warning("scheduler_state: %s ga yozib bo'lmadi: %s", _PATH, e)
