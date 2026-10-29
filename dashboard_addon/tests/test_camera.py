"""«Yangi kamera»: day numbers, prayer times, zone drawing, Excel, the installer, and every camera page on a
temporary camera database (the real camera_service\\data\\camera.db is never opened)."""

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
"""
SETTINGS = {"target_fps": 5.0, "channel": 101, "model": "yolo26m.pt", "imgsz": 640, "desk_conf": 0.35, "anchor": "upper",
            "roi_margin": 0.25, "sit_window_s": 10.0, "sit_ratio": 0.7, "away_grace_s": 600.0, "gap_s": 60.0,
            "stall_s": 30.0, "timezone": "Asia/Tashkent", "prayer_credit": True, "prayer_window_min": 30,
            "prayer_max_min": 20, "prayer_offset_min": 0}
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


def peshin(day: date = DAY) -> float:
    from camera import prayer

    return prayer.times_for(day, tz())["peshin"].timestamp()


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
    p = peshin()
    rows = [  # desk, start, end, reason
        (1, ts("09:05"), p - 120, "away"), (1, p + 1080, ts("18:55"), "away"),  # 20 min out around Peshin
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
                                          DAY, tz(), store.settings(), now_ts=end + 3600)
    by_name = {r.employee: r for r in rows}
    nodir, husniddin, absent = by_name["Nodir"], by_name["Husniddin"], by_name["Muhammadumar"]
    p = peshin()
    assert nodir.break_count == 1
    assert round(nodir.prayer_s / 60) == 18  # break from Peshin-2 to Peshin+18 -> 18 minutes inside the window
    assert round(nodir.sat_s) == round((p - 120 - ts("09:05")) + (ts("18:55") - p - 1080))
    assert nodir.required_s == 600 * 60 and nodir.diff_s == nodir.work_s - nodir.required_s
    assert husniddin.break_count == 0 and husniddin.breaks == []  # the outage is not a break
    assert round(husniddin.no_data_s / 60) == 30 and round(husniddin.sat_s / 3600, 2) == 9.0
    assert absent.sat_s == 0 and absent.first is None
    assert gaps == [(ts("14:00"), ts("14:30"))]
    assert 0.94 < share < 0.96  # 30 of 600 minutes without data
    assert round(sum(nodir.hours.values())) == round(nodir.sat_s / 60)


def test_prayer_credit_is_capped_and_only_inside_the_window():
    from camera import report

    p = peshin()
    rows = pd.DataFrame({"start_ts": [ts("09:00"), p + 3600], "end_ts": [p - 600, ts("19:00")], "desk": ["A", "A"]})
    employee = {"id": 1, "name": "X", "work_start": "09:00", "work_end": "19:00", "required_minutes": 600,
                "workdays": "1,2,3,4,5,6"}
    day = report.employee_day(employee, rows, DAY, tz(), SETTINGS, [])
    assert round(day.prayer_s / 60) == 20  # 70-minute break: 30-minute window, capped at 20
    no_credit = report.employee_day(employee, rows, DAY, tz(), {**SETTINGS, "prayer_credit": False}, [])
    assert no_credit.prayer_s == 0


def test_hours_and_merge_helpers():
    from camera import report

    assert report.merge([(5, 7), (1, 3), (2, 4)]) == [(1, 4), (5, 7)]
    assert report.subtract((0, 10), [(2, 3), (5, 12)]) == [(0, 2), (3, 5)]
    minutes = report.hour_minutes([(ts("09:30"), ts("11:15"))], tz())
    assert {h: round(m) for h, m in minutes.items()} == {9: 30, 10: 60, 11: 15}
    assert report.hhmm(5400) == "1:30" and report.hhmm(-600, signed=True) == "-0:10" and report.hhmm(60, True) == "+0:01"


def test_prayer_times_for_tashkent_are_plausible():
    from camera import prayer

    times = prayer.times_for(DAY, tz())
    order = [times[name] for name in prayer.NAMES]
    assert order == sorted(order)
    assert time(11, 55) <= times["peshin"].time() <= time(12, 25)  # solar noon in Tashkent, October
    assert time(17, 15) <= times["shom"].time() <= time(17, 50)
    assert time(6, 30) <= times["quyosh"].time() <= time(7, 5)
    summer = prayer.times_for(date(2026, 6, 21), tz())
    assert time(19, 45) <= summer["shom"].time() <= time(20, 15)  # Tashkent sunset at the solstice ~19:58


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
    assert list(table["Hodim"]) == ["Nodir", "Husniddin", "Muhammadumar"]
    assert table.loc[1, "Ma'lumot yo'q"] == "0:30"


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
    summary, matrix = app.dataframe[0].value, app.dataframe[1].value
    assert "Nodir" in set(summary["Hodim"])
    assert yesterday.strftime("%d.%m") in matrix.columns
    assert matrix.loc[matrix["Hodim"] == "Nodir", yesterday.strftime("%d.%m")].item() == "2:30"


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
