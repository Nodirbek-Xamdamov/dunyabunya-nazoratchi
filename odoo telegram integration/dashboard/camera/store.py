"""Access to the camera service's database (SQLite, WAL) and its newest frame.

The file is made by the camera service (camera_service\\tools\\init_db.py); the dashboard never creates it.
Reads open it read-only; edits of employees, desks and settings use short write transactions, and the
service picks them up within about 10 seconds. Paths: CAMERA_DB_PATH / CAMERA_LIVE_DIR in dashboard\\.env,
or DASHBOARD_CAMERA_DB_PATH / DASHBOARD_CAMERA_LIVE_DIR in the environment (tests)."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from core.settings import ENV_FILE, parse_env_file

DEFAULT_DATA_DIR = Path(r"D:\Demos\Kamera\camera_service\data")
SETTING_KEYS = ("target_fps", "channel", "model", "imgsz", "desk_conf", "anchor", "roi_margin", "sit_window_s",
                "sit_ratio", "away_grace_s", "gap_s", "stall_s", "timezone", "prayer_credit", "prayer_window_min",
                "prayer_max_min", "prayer_offset_min")


class CameraDbMissing(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    db: Path
    live: Path


def paths() -> Paths:
    values = parse_env_file(ENV_FILE)
    db = os.environ.get("DASHBOARD_CAMERA_DB_PATH") or values.get("CAMERA_DB_PATH") or DEFAULT_DATA_DIR / "camera.db"
    live = os.environ.get("DASHBOARD_CAMERA_LIVE_DIR") or values.get("CAMERA_LIVE_DIR") or DEFAULT_DATA_DIR / "live"
    return Paths(Path(db), Path(live))


def _connect(write: bool = False) -> sqlite3.Connection:
    path = paths().db
    if not path.is_file():
        raise CameraDbMissing(str(path))
    if write:
        conn = sqlite3.connect(path, timeout=10)
    else:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


# --------------------------------------------------------------------------- reading


def settings() -> dict:
    with closing(_connect()) as conn:
        return {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT key, value FROM cam_settings")}


def employees(active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM cam_employees" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
    with closing(_connect()) as conn:
        return [dict(row) for row in conn.execute(sql)]


def desks(active_only: bool = True) -> list[dict]:
    sql = ("SELECT d.*, e.name AS employee FROM cam_desks d LEFT JOIN cam_employees e ON e.id = d.employee_id"
           + (" WHERE d.active = 1" if active_only else "") + " ORDER BY d.sort, d.id")
    with closing(_connect()) as conn:
        return [{**dict(row), "zone": json.loads(row["zone"] or "[]")} for row in conn.execute(sql)]


def sources() -> list[str]:
    with closing(_connect()) as conn:
        found = [row[0] for row in conn.execute("SELECT DISTINCT source FROM cam_intervals ORDER BY source")]
    return ["live"] + [s for s in found if s != "live"]


def intervals(start_ts: float, end_ts: float, source: str = "live") -> pd.DataFrame:
    """Intervals that overlap [start_ts, end_ts). An open interval (end_ts NULL) ends at its last_seen_ts."""
    sql = ("SELECT i.id, i.desk_id, i.employee_id, e.name AS employee, d.name AS desk, i.start_ts, i.end_ts,"
           " i.last_seen_ts, i.close_reason FROM cam_intervals i JOIN cam_desks d ON d.id = i.desk_id"
           " LEFT JOIN cam_employees e ON e.id = i.employee_id"
           " WHERE i.source = ? AND i.start_ts < ? AND COALESCE(i.end_ts, i.last_seen_ts) > ? ORDER BY i.start_ts")
    with closing(_connect()) as conn:
        frame = pd.read_sql_query(sql, conn, params=(source, end_ts, start_ts))
    frame["open"] = frame["end_ts"].isna()
    frame["end_ts"] = frame["end_ts"].fillna(frame["last_seen_ts"])
    return frame


def coverage(start_ts: float, end_ts: float, source: str = "live") -> pd.DataFrame:
    sql = ("SELECT minute_ts, expected_frames, processed_frames, connected_s FROM cam_coverage"
           " WHERE source = ? AND minute_ts >= ? AND minute_ts < ? ORDER BY minute_ts")
    with closing(_connect()) as conn:
        return pd.read_sql_query(sql, conn, params=(source, int(start_ts // 60 * 60), end_ts))


def status() -> dict:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT key, value, updated_at FROM cam_status").fetchall()
    values = {row["key"]: json.loads(row["value"]) for row in rows}
    values["_updated_at"] = max((row["updated_at"] for row in rows), default=None)
    return values


def events(limit: int = 30) -> list[dict]:
    with closing(_connect()) as conn:
        return [dict(row) for row in conn.execute("SELECT ts, kind, message FROM cam_events ORDER BY ts DESC LIMIT ?",
                                                  (limit,))]


def latest_frame(raw: bool = False) -> tuple[bytes | None, dict]:
    """Newest frame written by the service (raw = without drawings, for drawing zones) and its metadata."""
    folder = paths().live
    meta: dict = {}
    try:
        meta = json.loads((folder / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    for _ in range(3):  # the service may be replacing the file at this very moment
        try:
            return (folder / ("latest_raw.jpg" if raw else "latest.jpg")).read_bytes(), meta
        except FileNotFoundError:
            return None, meta
        except OSError:
            time.sleep(0.05)
    return None, meta


# --------------------------------------------------------------------------- editing


def save_settings(values: dict) -> None:
    now = time.time()
    with closing(_connect(write=True)) as conn, conn:
        conn.executemany("INSERT INTO cam_settings(key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE"
                         " SET value = excluded.value, updated_at = excluded.updated_at",
                         [(key, json.dumps(value), now) for key, value in values.items() if key in SETTING_KEYS])


def save_employee(employee_id: int | None, name: str, work_start: str, work_end: str, required_minutes: int,
                  workdays: str, active: bool) -> int:
    now = time.time()
    with closing(_connect(write=True)) as conn, conn:
        if employee_id is None:
            cur = conn.execute("INSERT INTO cam_employees(name, work_start, work_end, required_minutes, workdays, active,"
                               " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (name, work_start, work_end, required_minutes, workdays, int(active), now, now))
            return int(cur.lastrowid)
        conn.execute("UPDATE cam_employees SET name = ?, work_start = ?, work_end = ?, required_minutes = ?, workdays = ?,"
                     " active = ?, updated_at = ? WHERE id = ?",
                     (name, work_start, work_end, required_minutes, workdays, int(active), now, employee_id))
        return employee_id


def save_desk(desk_id: int | None, name: str, employee_id: int | None, zone: list[list[float]]) -> int:
    now = time.time()
    zone_json = json.dumps([[round(x, 4), round(y, 4)] for x, y in zone])
    with closing(_connect(write=True)) as conn, conn:
        if desk_id is None:
            sort = conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM cam_desks").fetchone()[0]
            cur = conn.execute("INSERT INTO cam_desks(name, employee_id, zone, sort, active, updated_at)"
                               " VALUES (?, ?, ?, ?, 1, ?)", (name, employee_id, zone_json, sort, now))
            return int(cur.lastrowid)
        conn.execute("UPDATE cam_desks SET name = ?, employee_id = ?, zone = ?, updated_at = ? WHERE id = ?",
                     (name, employee_id, zone_json, now, desk_id))
        return desk_id


def delete_desk(desk_id: int) -> None:
    """Soft delete: the desk's past intervals stay in the reports."""
    with closing(_connect(write=True)) as conn, conn:
        conn.execute("UPDATE cam_desks SET active = 0, zone = '[]', updated_at = ? WHERE id = ?", (time.time(), desk_id))
