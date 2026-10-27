"""Rolling buffer: delete day folders older than retention_days under <video_root>/<camera_id>/.

Only folders named YYYY-MM-DD are touched. keep_root (truth-set clips) is a different tree and
is never looked at here.
"""
from __future__ import annotations

import logging
import re
import shutil
from datetime import date, timedelta
from pathlib import Path

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
log = logging.getLogger("w4-recorder.retention")


def expired_days(camera_dir: Path, today: date, keep_days: int) -> list[Path]:
    """Day folders outside the last keep_days days (14 = today and the 13 days before it)."""
    if not camera_dir.is_dir():
        return []
    oldest_kept = today - timedelta(days=keep_days - 1)
    out = []
    for p in camera_dir.iterdir():
        if p.is_dir() and DAY_RE.match(p.name):
            try:
                day = date.fromisoformat(p.name)
            except ValueError:
                continue
            if day < oldest_kept:
                out.append(p)
    return sorted(out)


def purge(camera_dir: Path, today: date, keep_days: int, keep_root: Path | None = None) -> list[str]:
    removed = []
    for folder in expired_days(camera_dir, today, keep_days):
        if keep_root and keep_root.resolve() in (folder.resolve(), *folder.resolve().parents):
            continue  # never inside the keep tree
        size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
        shutil.rmtree(folder)
        removed.append(folder.name)
        log.info("bufer: eski kun o'chirildi", extra={"day": folder.name, "bytes": size})
    return removed
