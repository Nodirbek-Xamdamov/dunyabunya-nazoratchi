"""«Yangi kamera»: day numbers (keldi, ketdi, tashqarida), the 5-second videos, one employee - one desk, zone
drawing, Excel, the installer, and every camera page on a temporary camera database (the real
camera_service\\data\\camera.db is never opened)."""

from __future__ import annotations

import json
import os
import sqlite3
import time as time_mod
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import DASHBOARD_DIR

DAY = date(2026, 10, 20)  # a Tuesday
# Mirror of camera_service\camsvc\db.py SCHEMA (the service owns the file; tests need their own copy)
SCHEMA = """
CREATE TABLE cam_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE cam_employees (id INTEGER PRIMARY KEY, name TEXT NOT NULL, work_start TEXT NOT NULL DEFAULT '09:00',
    work_end TEXT NOT NULL DEFAULT '19:00', required_minutes INTEGER NOT NULL DEFAULT 600,
    workdays TEXT NOT NULL DEFAULT '1,2,3,4,5,6', active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
    updated_at REAL NOT NULL);
CREATE TABLE cam_desks (id INTEGER PRIMARY KEY, name TEXT NOT NULL, employee_id INTEGER, zone TEXT NOT NULL DEFAULT '[]',
    sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL);
CREATE TABLE cam_intervals (id INTEGER PRIMARY KEY, desk_id INTEGER NOT NULL, employee_id INTEGER, start_ts REAL NOT NULL,
    end_ts REAL, last_seen_ts REAL NOT NULL, close_reason TEXT, source TEXT NOT NULL DEFAULT 'live',
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE cam_coverage (source TEXT NOT NULL DEFAULT 'live', minute_ts INTEGER NOT NULL, expected_frames INTEGER NOT NULL,
    processed_frames INTEGER NOT NULL, camera_frames INTEGER NOT NULL DEFAULT 0, connected_s REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (source, minute_ts));
CREATE TABLE cam_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE cam_status (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE cam_events (id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE cam_clips (id INTEGER PRIMARY KEY, source TEXT NOT NULL DEFAULT 'live', desk_id INTEGER, employee_id INTEGER,
    interval_id INTEGER, kind TEXT NOT NULL, event_ts REAL NOT NULL, path TEXT NOT NULL, created_at REAL NOT NULL);
"""
SETTINGS = {"target_fps": 5.0, "channel": 101, "model": "yolo26m.pt", "imgsz": 640, "desk_conf": 0.35, "anchor": "upper",
            "roi_margin": 0.25, "sit_window_s": 10.0, "sit_ratio": 0.7, "away_grace_s": 600.0, "gap_s": 60.0,
            "stall_s": 30.0, "timezone": "Asia/Tashkent", "clip_keep_days": 30}
INSTALLER = Path(r"D:\Demos\Kamera\dashboard_addon\install.py")


def jpeg(width: int = 64, height: int = 36) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height), (120, 120, 120)).save(buffer, "JPEG")
    return buffer.getvalue()


def tz():
    from core.settings import load_settings

    return load_settings().tz


def ts(hhmm: str, day: date = DAY) -> float:
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(hours, minutes), tzinfo=tz()).timestamp()


@pytest.fixture()
def camera_db(tmp_path):
    path = tmp_path / "camera.db"
    live = tmp_path / "live"
    live.mkdir()
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    now = time_mod.time()
    conn.executemany("INSERT INTO cam_settings VALUES (?, ?, ?)", [(k, json.dumps(v), now) for k, v in SETTINGS.items()])
    for i, (desk, person) in enumerate([("Uzoq stol", "Nodir"), ("O'rta stol", "Husniddin"), ("Yaqin stol", "Muhammadumar")],
                                       start=1):
        conn.execute("INSERT INTO cam_employees(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)", (i, person, now, now))
        zone = json.dumps([[0.1 * i, 0.5], [0.1 * i + 0.08, 0.5], [0.1 * i + 0.08, 0.9], [0.1 * i, 0.9]])
        conn.execute("INSERT INTO cam_desks(id, name, employee_id, zone, sort, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (i, desk, i, zone, i, now))
    rows = [  # desk, start, end, reason
        (1, ts("09:05"), ts("12:10"), "away"), (1, ts("12:30"), ts("18:55"), "away"),  # 20 min outside
        (2, ts("09:30"), ts("14:00"), "no_data"), (2, ts("14:30"), ts("19:00"), "away"),  # camera down 14:00-14:30
    ]
    for desk, start, end, reason in rows:
        conn.execute("INSERT INTO cam_intervals(desk_id, employee_id, start_ts, end_ts, last_seen_ts, close_reason, created_at,"
                     " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (desk, desk, start, end, end, reason, now, now))
    minute = int(ts("09:00"))
    while minute < ts("19:00"):
        if not ts("14:00") <= minute < ts("14:30"):
            conn.execute("INSERT INTO cam_coverage VALUES ('live', ?, 300, 290, 1300, 60)", (minute,))
        minute += 60
    conn.execute("INSERT INTO cam_status VALUES ('heartbeat', ?, ?)", (json.dumps(now), now))
    conn.execute("INSERT INTO cam_status VALUES ('desks', ?, ?)",
                 (json.dumps([{"desk_id": 1, "desk": "Uzoq stol", "employee": "Nodir", "state": "sitting", "since": now - 60}]),
                  now))
    conn.execute("INSERT INTO cam_events(ts, kind, message) VALUES (?, 'started', 'xizmat ishga tushdi')", (now,))
    clips = tmp_path / "clips"
    for kind, moment in (("in", "09:05"), ("out", "12:10"), ("in", "12:30")):  # no video for 18:55
        rel = f"live/{DAY.isoformat()}/1_{moment.replace(':', '-')}-00_{kind}.mp4"
        (clips / rel).parent.mkdir(parents=True, exist_ok=True)
        (clips / rel).write_bytes(b"\x00\x00\x00\x18ftypmp42")
        conn.execute("INSERT INTO cam_clips(source, desk_id, employee_id, interval_id, kind, event_ts, path, created_at)"
                     " VALUES ('live', 1, 1, 1, ?, ?, ?, ?)", (kind, ts(moment), rel, now))
    conn.commit()
    conn.close()
    (live / "latest.jpg").write_bytes(jpeg())
    (live / "latest_raw.jpg").write_bytes(jpeg())
    (live / "latest.json").write_text(json.dumps({"ts": now, "width": 64, "height": 36, "source": "live"}), "utf-8")
    os.environ["DASHBOARD_CAMERA_DB_PATH"] = str(path)
    os.environ["DASHBOARD_CAMERA_LIVE_DIR"] = str(live)
    yield path
    os.environ.pop("DASHBOARD_CAMERA_DB_PATH", None)
    os.environ.pop("DASHBOARD_CAMERA_LIVE_DIR", None)


# --------------------------------------------------------------------------- numbers


def test_day_report_numbers(camera_db):
    from camera import report, store

    start, end = report.day_bounds(DAY, tz())
    rows, gaps, share = report.day_report(store.employees(), store.intervals(start, end), store.coverage(start, end),
                                          DAY, tz(), now_ts=end + 3600)
    by_name = {r.employee: r for r in rows}
    nodir, husniddin, absent = by_name["Nodir"], by_name["Husniddin"], by_name["Muhammadumar"]
    assert (nodir.first, nodir.last, nodir.still_in) == (ts("09:05"), ts("18:55"), False)
    assert [(o.left, o.back, o.outside_s) for o in nodir.outings] == [(ts("12:10"), ts("12:30"), 1200.0)]
    assert nodir.outside_s == 1200.0
    assert husniddin.outings == [] and husniddin.outside_s == 0  # the camera outage is not time outside
    assert round(husniddin.no_data_s / 60) == 30 and husniddin.last == ts("19:00")
    assert absent.first is None and absent.outings == []
    assert gaps == [(ts("14:00"), ts("14:30"))]
    assert 0.94 < share < 0.96  # 30 of 600 minutes without data


def test_outing_partly_without_data_and_open_interval():
    from camera import report

    rows = pd.DataFrame({"start_ts": [ts("09:00"), ts("11:00")], "end_ts": [ts("10:00"), ts("12:00")], "desk": ["A", "A"],
                         "open": [False, True]})
    employee = {"id": 1, "name": "X", "work_start": "09:00", "work_end": "19:00", "workdays": "1,2,3,4,5,6"}
    day = report.employee_day(employee, rows, DAY, tz(), [(ts("10:30"), ts("10:45"))])
    assert [(o.outside_s, o.no_data_s) for o in day.outings] == [(2700.0, 900.0)]
    assert day.still_in  # the last interval is still open: "hali xonada"


def test_merge_helpers_and_formatting():
    from camera import report

    assert report.merge([(5, 7), (1, 3), (2, 4)]) == [(1, 4), (5, 7)]
    assert report.subtract((0, 10), [(2, 3), (5, 12)]) == [(0, 2), (3, 5)]
    assert report.hhmm(5400) == "1:30" and report.hhmm(-600) == "-0:10" and report.hhmm(None) == "—"
    assert report.clock(ts("09:05"), tz(), seconds=True) == "09:05:00"


def test_videos_are_matched_to_arrivals_and_departures(camera_db):
    from camera import report, store
    from camera import ui as cu

    start, end = report.day_bounds(DAY, tz())
    rows, _gaps, _share = report.day_report(store.employees(), store.intervals(start, end), store.coverage(start, end),
                                            DAY, tz(), now_ts=end + 3600)
    nodir = next(r for r in rows if r.employee == "Nodir")
    clips = store.clips(start, end)
    found = [cu.find_clip(clips, 1, kind, moment) for _label, kind, moment in cu.moments(nodir)]
    assert [f is not None for f in found] == [True, True, True, False]
    assert store.clip_file(found[0]) is not None
    assert store.clip_file("../camera.db") is None  # never outside the clips folder


def test_one_employee_sits_at_one_desk(camera_db):
    from camera import store

    with pytest.raises(store.EmployeeTaken):
        store.save_desk(None, "Ikkinchi stol", 1, [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]])
    store.save_desk(1, "Uzoq stol", 1, [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]])  # the same desk again is fine


# --------------------------------------------------------------------------- zone drawing, Excel, installer


def test_zone_selection_parsing():
    from camera import zone_editor

    click = {"selection": {"points": [{"x": 960.0, "y": 540.0}], "lasso": []}}
    assert zone_editor.read_selection(click, 1920, 1080) == ("add", [[0.5, 0.5]])
    circle = [(960 + 300 * __import__("math").cos(a / 20), 540 + 200 * __import__("math").sin(a / 20)) for a in range(126)]
    lasso = {"selection": {"points": [], "lasso": [{"x": [p[0] for p in circle], "y": [p[1] for p in circle]}]}}
    kind, poly = zone_editor.read_selection(lasso, 1920, 1080)
    assert kind == "replace" and 6 <= len(poly) <= zone_editor.MAX_POINTS
    box = {"selection": {"points": [{"x": x, "y": y} for x in (10, 50, 90) for y in (10, 50, 90)], "lasso": []}}
    kind, hull = zone_editor.read_selection(box, 100, 100)
    assert kind == "replace" and len(hull) == 4
    assert zone_editor.read_selection({"selection": {"points": [], "lasso": []}}, 100, 100) is None
    assert zone_editor.read_selection(None, 100, 100) is None


def test_excel_has_both_sheets_and_neutralises_formulas():
    from io import BytesIO

    from openpyxl import load_workbook

    from camera.excel import workbook

    data = workbook({"Kunlik jamlanma": pd.DataFrame({"Hodim": ["=HYPERLINK(1)"]}), "Intervallar": pd.DataFrame({"a": [1]})})
    book = load_workbook(BytesIO(data))
    assert book.sheetnames == ["Kunlik jamlanma", "Intervallar"]
    assert book["Kunlik jamlanma"]["A2"].value == "'=HYPERLINK(1)"


def test_installer_patch_is_idempotent_and_reversible():
    import importlib.util
    import sys

    if not INSTALLER.exists():
        pytest.skip("the installer is kept in D:\\Demos\\Kamera\\dashboard_addon")
    spec = importlib.util.spec_from_file_location("cam_install", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["cam_install"] = module
    spec.loader.exec_module(module)
    original = (DASHBOARD_DIR / "app.py").read_text(encoding="utf-8")
    base = module.unpatched(original)
    once = module.patched(base)
    assert module.patched(once) == once
    assert module.unpatched(once) == base


# --------------------------------------------------------------------------- pages

PAGE_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from core.settings import load_settings
from ui import components as c, shell
c.inject_css()
shell.sidebar(load_settings())
from views import {name}
{name}.render()
"""


def run_page(name: str, state: dict | None = None) -> AppTest:
    app = AppTest.from_string(PAGE_SCRIPT.format(root=str(DASHBOARD_DIR), name=name), default_timeout=120)
    for key, value in (state or {}).items():
        app.session_state[key] = value
    app.run()
    return app


def assert_clean(app: AppTest) -> None:
    assert not app.exception, [item.value for item in app.exception]
    assert not app.error, [item.value for item in app.error]


@pytest.mark.parametrize("name", ["kamera_bugun", "kamera_kunlar", "kamera_jonli", "kamera_sozlash"])
def test_camera_pages_render(camera_db, name):
    assert_clean(run_page(name))


def test_today_page_shows_the_chosen_day(camera_db):
    app = run_page("kamera_bugun", {"cam_day_live": DAY})
    assert_clean(app)
    table = app.dataframe[0].value
    assert list(table.columns) == ["Hodim", "Keldi", "Ketdi", "Tashqarida", "Necha marta chiqdi"]
    assert list(table["Hodim"]) == ["Nodir", "Husniddin", "Muhammadumar"]
    assert list(table.loc[0, ["Keldi", "Ketdi", "Tashqarida", "Necha marta chiqdi"]]) == ["09:05", "18:55", "0:20", 1]
    assert table.loc[1, "Tashqarida"] == "0:00"  # 14:00-14:30 the camera was down: not counted as outside
    outings = app.dataframe[1].value
    assert list(outings.loc[0, ["Chiqdi", "Qaytdi", "Tashqarida"]]) == ["12:10:00", "12:30:00", "0:20"]
    assert app.warning  # the outage is reported


def test_days_page_last_7_days(camera_db):
    today = datetime.now(tz()).date()
    yesterday = next(d for d in (today - timedelta(days=3), today - timedelta(days=4)) if d != DAY)  # a day of its own
    conn = sqlite3.connect(camera_db)
    conn.execute("INSERT INTO cam_intervals(desk_id, employee_id, start_ts, end_ts, last_seen_ts, close_reason, created_at,"
                 " updated_at) VALUES (1, 1, ?, ?, ?, 'away', 0, 0)",
                 (ts("10:00", yesterday), ts("12:30", yesterday), ts("12:30", yesterday)))
    conn.commit()
    conn.close()
    app = run_page("kamera_kunlar", {"_url_loaded": True, "f_preset": "last7"})
    assert_clean(app)
    table = app.dataframe[0].value
    line = table[(table["Sana"] == yesterday.strftime("%d.%m.%Y")) & (table["Hodim"] == "Nodir")]
    assert list(line[["Keldi", "Ketdi", "Tashqarida"]].iloc[0]) == ["10:00", "12:30", "0:00"]


def test_missing_camera_database_gives_a_message(tmp_path):
    os.environ["DASHBOARD_CAMERA_DB_PATH"] = str(tmp_path / "none.db")
    try:
        app = run_page("kamera_bugun")
        assert not app.exception
        assert app.error and "topilmadi" in app.error[0].value
    finally:
        os.environ.pop("DASHBOARD_CAMERA_DB_PATH", None)


def test_settings_save_and_employee_edit(camera_db):
    from camera import store

    store.save_settings({"away_grace_s": 300.0, "not_a_setting": 1})
    assert store.settings()["away_grace_s"] == 300.0 and "not_a_setting" not in store.settings()
    new_id = store.save_employee(None, "Ali", "09:00", "18:00", 540, "1,2,3,4,5", True)
    assert {e["name"] for e in store.employees()} >= {"Ali", "Nodir"}
    desk = store.save_desk(None, "Yangi", new_id, [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]])
    assert any(d["id"] == desk and len(d["zone"]) == 3 for d in store.desks())
    store.delete_desk(desk)
    assert all(d["id"] != desk for d in store.desks())
