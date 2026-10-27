"""Retention (W0-08; D-27, D-13, D-29). Dry-run by default: retention.delete_enabled stays false until the user has
seen the first list.

Video: only folders <video_root>\\<camera>\\<YYYY-MM-DD> whose day is older than retention.video_days are candidates.
Never deleted (protected): anything equal to / inside retention.protect_paths (old project's E:\\Kamera raw data,
keep sets), a day folder holding a KEEP or .keep file, days marked video_segments.is_keep, links/junctions, folders
not named like a date ("backup" ...). A video_root that is itself inside a protected path is refused entirely.
Text: relevance=no episodes past retain_until (72 h, D-27) -> core.purge_unrelated_text().
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MARKERS = ("KEEP", ".keep")


@dataclass
class VideoPlan:
    root: str
    cutoff: str
    candidates: list[dict] = field(default_factory=list)  # {path, camera_id, day, bytes, files}
    protected: list[dict] = field(default_factory=list)   # {path, reason}
    refused: str | None = None

    @property
    def total_bytes(self) -> int:
        return sum(c["bytes"] for c in self.candidates)


def _norm(p: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(p))).rstrip("\\/")


def _inside(path: Path | str, parent: Path | str) -> bool:
    a, b = _norm(path), _norm(parent)
    return a == b or a.startswith(b + os.sep)


def _is_link(p: Path) -> bool:
    return p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())


def _size(folder: Path) -> tuple[int, int]:
    total = files = 0
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        dirnames[:] = [d for d in dirnames if not _is_link(Path(dirpath) / d)]
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
                files += 1
            except OSError:
                pass
    return total, files


def plan_video(root: Path, keep_days: int, today: date, protect: list[str],
               db_keep: set[tuple[str, str]] = frozenset()) -> VideoPlan:
    cutoff = today - timedelta(days=keep_days)
    plan = VideoPlan(root=str(root), cutoff=cutoff.isoformat())
    for prot in protect:
        if _inside(root, prot):
            plan.refused = f"video_root {root} himoyalangan papka ichida ({prot})"
            return plan
    if not root.is_dir():
        return plan
    for cam in sorted(root.iterdir()):
        if not cam.is_dir():
            continue
        if _is_link(cam) or any(_inside(cam, prot) for prot in protect):
            plan.protected.append({"path": str(cam), "reason": "himoyalangan yoki havola"})
            continue
        for day in sorted(cam.iterdir()):
            if not day.is_dir() or not DAY.match(day.name):
                continue  # 'backup' and anything else that is not a day folder is never touched
            try:
                d = date.fromisoformat(day.name)
            except ValueError:
                continue
            if d >= cutoff:
                continue
            reason = None
            if _is_link(day):
                reason = "havola (junction/symlink)"
            elif any(_inside(day, prot) for prot in protect):
                reason = "retention.protect_paths ichida"
            elif any((day / m).exists() for m in MARKERS):
                reason = "KEEP belgisi"
            elif (cam.name, day.name) in db_keep:
                reason = "video_segments.is_keep"
            if reason:
                plan.protected.append({"path": str(day), "reason": reason})
                continue
            size, files = _size(day)
            plan.candidates.append({"path": str(day), "camera_id": cam.name, "day": day.name, "bytes": size, "files": files})
    return plan


def delete_video(plan: VideoPlan, root: Path, protect: list[str]) -> list[dict]:
    """Delete the planned day folders; every path is re-checked right before deletion."""
    done = []
    for c in plan.candidates:
        p = Path(c["path"])
        if not _inside(p, root) or _norm(p) == _norm(root) or any(_inside(p, prot) for prot in protect):
            done.append({**c, "deleted": False, "error": "tekshiruvdan o'tmadi"})
            continue
        if _is_link(p) or any((p / m).exists() for m in MARKERS) or not DAY.match(p.name):
            done.append({**c, "deleted": False, "error": "KEEP/havola/nom"})
            continue
        try:
            shutil.rmtree(p)
            done.append({**c, "deleted": True})
        except OSError as exc:
            done.append({**c, "deleted": False, "error": repr(exc)})
    return done


def db_keep_days(conn) -> set[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT camera_id, local_day::text FROM core.video_segments WHERE is_keep")
        return {(a, b) for a, b in cur.fetchall()}


def text_due(conn, now: datetime) -> dict:
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*), min(retain_until) FROM core.episodes
                       WHERE relevance = 'no' AND retain_until <= %s AND text_purged_at IS NULL""", (now,))
        n, oldest = cur.fetchone()
    return {"episodes_due": n, "oldest_retain_until": oldest.isoformat() if oldest else None}


def run(conn, now: datetime, cfg: dict, delete_enabled: bool | None = None) -> dict:
    """One retention pass; dry-run unless retention.delete_enabled (or `delete_enabled`) is true."""
    enabled = bool(cfg["retention.delete_enabled"]) if delete_enabled is None else delete_enabled
    root = Path(cfg["retention.video_root"])
    protect = list(cfg["retention.protect_paths"])
    plan = plan_video(root, int(cfg["retention.video_days"]), now.date(), protect, db_keep_days(conn))
    result = {"mode": "delete" if enabled else "dry-run", "video_root": str(root), "cutoff_day": plan.cutoff,
              "refused": plan.refused, "video_candidates": plan.candidates, "video_protected": plan.protected,
              "video_bytes": plan.total_bytes, "text": text_due(conn, now)}
    if enabled and not plan.refused:
        deleted = delete_video(plan, root, protect)
        with conn.cursor() as cur:
            for d in deleted:
                if d["deleted"]:
                    cur.execute("UPDATE core.video_segments SET deleted_at = %s WHERE camera_id = %s AND local_day = %s "
                                "AND deleted_at IS NULL", (now, d["camera_id"], d["day"]))
            cur.execute("SELECT core.purge_unrelated_text(%s)", (now,))
            result["text_purged"] = cur.fetchone()[0]
        result["video_deleted"] = deleted
    return result
