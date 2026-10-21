"""Camera database: SQLite in WAL mode, camera_service/data/camera.db, every table starts with cam_.

Writers: the camera service (intervals, coverage, status, events) and the dashboard's "Yangi kamera"
pages (employees, desks, settings). Times are UTC epoch seconds; the dashboard shows Asia/Tashkent.
Desk zones are stored as fractions of the frame (0..1), so they fit both camera streams.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from camsvc.config import SERVICE_DIR

# CAMERA_DB_PATH points the service and tools at another file (tests, trial runs)
DB_PATH = Path(os.environ.get("CAMERA_DB_PATH") or SERVICE_DIR / "data" / "camera.db")
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS cam_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cam_employees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    work_start TEXT NOT NULL DEFAULT '09:00',      -- local time HH:MM
    work_end TEXT NOT NULL DEFAULT '19:00',
    required_minutes INTEGER NOT NULL DEFAULT 600,
    workdays TEXT NOT NULL DEFAULT '1,2,3,4,5,6',   -- ISO weekdays, 1 = Monday
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cam_desks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    employee_id INTEGER REFERENCES cam_employees(id) ON DELETE SET NULL,
    zone TEXT NOT NULL DEFAULT '[]',                -- JSON [[x, y], ...] as fractions of the frame
    sort INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cam_intervals (
    id INTEGER PRIMARY KEY,
    desk_id INTEGER NOT NULL REFERENCES cam_desks(id),
    employee_id INTEGER REFERENCES cam_employees(id) ON DELETE SET NULL,
    start_ts REAL NOT NULL,
    end_ts REAL,                                    -- NULL while the person is still at the desk
    last_seen_ts REAL NOT NULL,
    close_reason TEXT,                              -- away | no_data | stopped
    source TEXT NOT NULL DEFAULT 'live',            -- live | clip:<file name>
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS cam_intervals_start ON cam_intervals(source, start_ts);
CREATE INDEX IF NOT EXISTS cam_intervals_desk ON cam_intervals(desk_id, start_ts);
CREATE TABLE IF NOT EXISTS cam_coverage (
    source TEXT NOT NULL DEFAULT 'live',
    minute_ts INTEGER NOT NULL,                     -- UTC epoch of the minute start
    expected_frames INTEGER NOT NULL,
    processed_frames INTEGER NOT NULL,
    camera_frames INTEGER NOT NULL DEFAULT 0,
    connected_s REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (source, minute_ts)
);
CREATE TABLE IF NOT EXISTS cam_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cam_status (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cam_events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,                             -- started | stopped | connected | disconnected | error | info
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cam_events_ts ON cam_events(ts);
"""

DEFAULT_SETTINGS = {
    "target_fps": 5.0,
    "channel": 101,
    "model": "yolo26m.pt",
    "imgsz": 640,
    "desk_conf": 0.35,
    "anchor": "center",            # center | upper (point at 30% of the box height)
    "roi_margin": 0.25,            # model runs on the zones' bounding box plus this margin; null = whole frame
    "sit_window_s": 10.0,
    "sit_ratio": 0.70,
    "away_grace_s": 600.0,         # user: 10 minutes
    "gap_s": 60.0,
    "stall_s": 30.0,
    "timezone": "Asia/Tashkent",
    "prayer_credit": True,
    "prayer_window_min": 30,       # absence that starts within this many minutes after a prayer time
    "prayer_max_min": 20,          # credited at most this long per prayer
    "prayer_offset_min": 0,
}


def connect(path: Path = DB_PATH, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init(conn: sqlite3.Connection) -> None:
    with conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO cam_meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        now = time.time()
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO cam_settings(key, value, updated_at) VALUES (?, ?, ?)",
                         (key, json.dumps(value), now))


def settings(conn: sqlite3.Connection) -> dict:
    values = dict(DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key, value FROM cam_settings"):
        values[row["key"]] = json.loads(row["value"])
    return values


def settings_stamp(conn: sqlite3.Connection) -> float:
    """Changes when the dashboard edits settings, desks or employees (the service then reloads)."""
    stamps = [conn.execute(f"SELECT MAX(updated_at), COUNT(*) FROM {table}").fetchone()
              for table in ("cam_settings", "cam_desks", "cam_employees")]
    return sum((row[0] or 0) + row[1] for row in stamps)


def seed(conn: sqlite3.Connection, desks: list[tuple[str, str]], work_start: str = "09:00", work_end: str = "19:00",
         required_minutes: int = 600) -> int:
    """First fill of an empty database: (desk name, employee name) pairs, zones left empty."""
    if conn.execute("SELECT COUNT(*) FROM cam_desks").fetchone()[0]:
        return 0
    now = time.time()
    with conn:
        for sort, (desk, employee) in enumerate(desks, start=1):
            cur = conn.execute("INSERT INTO cam_employees(name, work_start, work_end, required_minutes, created_at,"
                               " updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                               (employee, work_start, work_end, required_minutes, now, now))
            conn.execute("INSERT INTO cam_desks(name, employee_id, sort, updated_at) VALUES (?, ?, ?, ?)",
                         (desk, cur.lastrowid, sort, now))
    return len(desks)


def set_status(conn: sqlite3.Connection, values: dict) -> None:
    now = time.time()
    with conn:
        conn.executemany("INSERT INTO cam_status(key, value, updated_at) VALUES (?, ?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                         [(k, json.dumps(v), now) for k, v in values.items()])


def event(conn: sqlite3.Connection, kind: str, message: str, ts: float | None = None) -> None:
    with conn:
        conn.execute("INSERT INTO cam_events(ts, kind, message) VALUES (?, ?, ?)", (ts or time.time(), kind, message))


def active_desks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT d.id, d.name, d.employee_id, d.zone, e.name AS employee FROM cam_desks d "
                        "LEFT JOIN cam_employees e ON e.id = d.employee_id WHERE d.active = 1 ORDER BY d.sort, d.id")
    return [{**dict(r), "zone": json.loads(r["zone"] or "[]")} for r in rows]


def open_interval(conn: sqlite3.Connection, desk_id: int, employee_id: int | None, start: float, source: str) -> int:
    now = time.time()
    with conn:
        cur = conn.execute("INSERT INTO cam_intervals(desk_id, employee_id, start_ts, last_seen_ts, source, created_at,"
                           " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (desk_id, employee_id, start, start, source, now, now))
    return int(cur.lastrowid)


def touch_interval(conn: sqlite3.Connection, interval_id: int, last_seen: float) -> None:
    with conn:
        conn.execute("UPDATE cam_intervals SET last_seen_ts = ?, updated_at = ? WHERE id = ?",
                     (last_seen, time.time(), interval_id))


def close_interval(conn: sqlite3.Connection, interval_id: int, end: float, reason: str) -> None:
    with conn:
        conn.execute("UPDATE cam_intervals SET end_ts = ?, last_seen_ts = ?, close_reason = ?, updated_at = ? WHERE id = ?",
                     (end, end, reason, time.time(), interval_id))


def open_intervals(conn: sqlite3.Connection, source: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM cam_intervals WHERE end_ts IS NULL AND source = ?", (source,)).fetchall()


def add_coverage(conn: sqlite3.Connection, source: str, minute_ts: int, expected: int, processed: int, camera: int,
                 connected_s: float) -> None:
    with conn:
        conn.execute("INSERT INTO cam_coverage(source, minute_ts, expected_frames, processed_frames, camera_frames,"
                     " connected_s) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(source, minute_ts) DO UPDATE SET"
                     " expected_frames = expected_frames + excluded.expected_frames,"
                     " processed_frames = processed_frames + excluded.processed_frames,"
                     " camera_frames = camera_frames + excluded.camera_frames,"
                     " connected_s = connected_s + excluded.connected_s",
                     (source, minute_ts, expected, processed, camera, connected_s))
