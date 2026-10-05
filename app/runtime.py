"""Jarayon haqidagi umumiy ma'lumot: qachon ishga tushgan, qaysi versiya, polling holati.

Alohida modul — `main` (hamma narsani import qiladi) bilan /status buyrug'i o'rtasida
aylanma import bo'lmasligi uchun: `main` bu yerga o'z holat-funksiyasini ro'yxatdan o'tkazadi.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

STARTED_AT = time.time()

# main.py o'rnatadi: () -> (sog'lommi, {"polling": ..., "seconds_since_last_poll": ...})
polling_health: Callable[[], tuple[bool, dict]] | None = None


def uptime_seconds() -> int:
    return int(time.time() - STARTED_AT)


def code_version() -> str:
    """Ishlayotgan kodning git belgisi ("fix/full-audit-2026-10-05@8e7c789") — qaysi
    versiya ishlab turganini bilish va orqaga qaytarish uchun. Git bo'lmasa "noma'lum"."""
    try:
        git_dir = Path(__file__).resolve().parent.parent / ".git"
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:7]
        ref = head.split(" ", 1)[1].strip()
        branch = ref.rsplit("/", 1)[-1] if ref.startswith("refs/heads/") else ref
        if ref.startswith("refs/heads/"):
            branch = ref[len("refs/heads/"):]
        ref_file = git_dir / ref
        if ref_file.exists():
            return f"{branch}@{ref_file.read_text(encoding='utf-8').strip()[:7]}"
        for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return f"{branch}@{line[:7]}"
        return branch
    except Exception:  # noqa: BLE001
        return "noma'lum"
