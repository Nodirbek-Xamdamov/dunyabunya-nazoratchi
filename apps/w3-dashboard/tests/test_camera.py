"""W3-04: the camera page on genesis_test (W0/W4 fixtures, api.v_staff_day only, read-only). Frames are
synthetic pictures drawn by scripts/make_fixture_frames.py into a temporary folder: no camera, no person,
no face. E:\\data is never read or written."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from core import camera as cam
from core import db, queries
from core.settings import load_settings
from scripts import make_fixture_frames as fixture_frames
from tests.test_views import PAGE_SCRIPT

STORED = r"E:\data\camera_events\2026\10\26\camera1\fixture_h1_0_in.jpg"


def _staff_days():
    with db.session(load_settings()) as session:
        return queries.staff_days(session, "2000-01-01", "2100-01-01")


def _days():
    return cam.days_from_frame(_staff_days(), load_settings().tz)


@pytest.fixture
def frames_dir(tmp_path):
    """Synthetic frames for every path the fixtures list, in a temporary folder."""
    items = [(f.path, item.staff_no, f.kind, cam.fmt_time(f.at)) for item in _days() for f in item.frames]
    written = fixture_frames.write_frames(tmp_path, items)
    assert written
    return tmp_path


def _settings_env(**values):
    for key, value in values.items():
        os.environ[f"W3_{key.upper()}"] = str(value)
    load_settings.cache_clear()


@pytest.fixture(autouse=True)
def clean_env():
    yield
    for key in ("W3_FRAMES_DIR", "W3_SHOW_FRAMES"):
        os.environ.pop(key, None)
    load_settings.cache_clear()


# --------------------------------------------------------------------------- frames (no database)


def test_frame_tail_keeps_only_the_d29_tail():
    assert cam.frame_tail(STORED) == ("2026", "10", "26", "camera1", "fixture_h1_0_in.jpg")
    assert cam.frame_tail("F:/other/camera_events/2026/10/26/cam2/a.png") == ("2026", "10", "26", "cam2", "a.png")
    for bad in (None, "", r"E:\data\video\2026\10\26\camera1\a.jpg", r"E:\data\camera_events\2026\10\camera1\a.jpg",
                r"E:\data\camera_events\2026\10\26\camera1\a.exe", r"E:\data\camera_events\2026\10\26\..\a.jpg",
                r"E:\data\camera_events\26\10\2026\camera1\a.jpg", r"E:\data\camera_events\2026\10\26\cam 1\a.jpg"):
        assert cam.frame_tail(bad) is None, bad


def test_frame_file_reads_only_under_the_frames_folder(tmp_path):
    root = tmp_path / "frames"
    target = root / "2026" / "10" / "26" / "camera1" / "fixture_h1_0_in.jpg"
    target.parent.mkdir(parents=True)
    fixture_frames.draw(1, "in", "09:16").save(target, "JPEG")
    (tmp_path / "secret.jpg").write_bytes(b"x")
    assert cam.frame_file(root, STORED) == target.resolve()
    assert cam.frame_file(root, STORED.replace("h1_0", "h9_9")) is None  # missing file
    assert cam.frame_file(root, r"E:\data\camera_events\..\..\secret.jpg") is None
    picture = cam.frame_jpeg(target)
    with Image.open(__import__("io").BytesIO(picture)) as image:
        assert image.format == "JPEG" and max(image.size) <= cam.FRAME_SIDE
    (root / "bad.jpg").write_bytes(b"not a picture")
    assert cam.frame_jpeg(root / "bad.jpg") is None


def test_synthetic_frames_refuse_the_real_camera_folder():
    with pytest.raises(SystemExit):
        fixture_frames.write_frames(Path("E:/data/camera_events"), [(STORED, 1, "in", "09:00")])


def test_synthetic_frame_is_a_flat_drawing():
    image = fixture_frames.draw(2, "out", "18:00")
    assert image.size == fixture_frames.SIZE
    colors = sorted(image.getcolors(maxcolors=4096) or (), reverse=True)
    flat = sum(count for count, _ in colors[:6])  # wall, floor, door, frame, marker, title bar
    assert flat / (image.size[0] * image.size[1]) > 0.95  # flat fills plus text only, not a photo


# --------------------------------------------------------------------------- the day model


def _row(**extra):
    base = {"day": "2026-10-26", "staff_no": 2, "camera_id": "camera1", "camera_name": "Kamera 1",
            "first_in_at": "2026-10-26 03:00:00.000000", "last_out_at": "2026-10-26 13:00:00.000000",
            "office_minutes": 600.0, "room_minutes": 540.0, "away_minutes": 60.0,
            "exits": [{"from": "2026-10-26T07:00:00+00:00", "to": "2026-10-26T08:00:00+00:00", "minutes": 60.0}],
            "frames": [{"kind": "in", "event_at": "2026-10-26T08:00:00+00:00", "frame_at": "2026-10-26T08:00:00+00:00",
                        "path": STORED, "inferred": True}],
            "first_in_frame": STORED, "last_out_frame": None, "uncertain": False, "uncertainty_note": None,
            "ai_label": "AI taxmini", "person_label": "Ism Familiya"}
    base.update(extra)
    return base


def test_day_model_follows_d32():
    tz = load_settings().tz
    item = cam.from_row(_row(), tz)
    assert item.name == "Hodim 2"  # never the view's label
    assert [cam.fmt_time(x) for pair in item.room_intervals() for x in pair] == ["08:00", "12:00", "13:00", "18:00"]
    assert item.exits[0].inferred and item.is_uncertain  # coming back was only inferred
    open_day = cam.from_row(_row(last_out_at=None, office_minutes=None, away_minutes=None, frames=[]), tz)
    assert open_day.room_intervals()[-1][1] is None and open_day.is_uncertain
    plain = cam.from_row(_row(frames=[]), tz)
    assert not plain.is_uncertain and not plain.exits[0].inferred


# --------------------------------------------------------------------------- data from genesis_test


def test_staff_days_match_the_fixtures():
    frame = _staff_days()
    assert len(frame) == 8  # 2 days x 4 permanent staff
    assert set(frame["staff_no"]) == {1, 2, 3, 4}
    assert set(frame["ai_label"]) == {"AI taxmini"}
    assert "person_label" not in frame.columns  # numbers only on the page
    for item in _days():
        if item.last_out is None:  # Hodim 3 on the second day: last exit not seen
            assert item.staff_no == 3 and item.uncertain and item.office_minutes is None
            continue
        office = (item.last_out - item.first_in).total_seconds() / 60
        assert item.office_minutes == pytest.approx(office, abs=0.1)
        assert item.room_minutes + item.away_minutes == pytest.approx(item.office_minutes, abs=0.1)
        assert sum(x.minutes for x in item.exits) == pytest.approx(item.away_minutes, abs=0.1)
        assert item.in_frame and item.out_frame
        for x in item.exits:
            assert x.minutes == pytest.approx((x.end - x.start).total_seconds() / 60, abs=0.1)
        room = sum((end - start).total_seconds() / 60 for start, end in item.room_intervals())
        assert room == pytest.approx(item.room_minutes, abs=0.1)
    inferred = [item for item in _days() if item.inferred_frames]
    assert inferred and {item.staff_no for item in inferred} == {2}


def test_camera_view_is_read_only():
    settings = load_settings()
    with db.session(settings) as session:
        with pytest.raises(db.DataError):
            session.rows("frames", "SELECT * FROM core.camera_event_frames LIMIT 1")
        with pytest.raises(db.WriteRefused):
            session.rows("w", "DELETE FROM api.v_staff_day")
        with pytest.raises(db.DataError):  # passes the SELECT/WITH check, the server refuses it
            session.rows("w", "WITH x AS (DELETE FROM api.v_staff_day RETURNING 1) SELECT * FROM x")
        assert session.scalar("n", "SELECT count(*) FROM api.v_staff_day") == 8


# --------------------------------------------------------------------------- the page


def _run_camera(query=None) -> AppTest:
    app = AppTest.from_string(PAGE_SCRIPT.format(root=str(Path(__file__).resolve().parent.parent), name="camera"),
                              default_timeout=120)
    for key, value in (query or {"p": "last7"}).items():
        app.query_params[key] = value
    app.run()
    assert not app.exception, [item.value for item in app.exception]
    assert not app.error, [item.value for item in app.error]
    return app


def _texts(app: AppTest) -> str:
    parts = [item.value for kind in ("markdown", "caption", "subheader") for item in app.get(kind)]
    parts += [item.proto.body for item in app.get("html")]
    return "\n".join(str(part) for part in parts)


def test_page_shows_every_staff_member_with_synthetic_frames(frames_dir):
    _settings_env(frames_dir=frames_dir)
    app = _run_camera()
    text = _texts(app)
    for n in range(1, 5):
        assert f"Hodim {n}" in text
    assert "Ism Familiya" not in text
    assert "AI taxmini" in text
    days = sorted({item.day for item in _days()}, reverse=True)
    assert app.selectbox(key="cam_day").value == days[0]
    images = app.get("image")
    assert images  # arrival / departure frames
    assert len(app.get("plotly_chart")) == 1  # the timeline
    assert "Oxirgi chiqish ko'rilmadi" in text  # Hodim 3, second day (the newest)


def test_page_other_day_and_missing_frames():
    days = sorted({item.day for item in _days()}, reverse=True)
    _settings_env(frames_dir=Path(__file__).parent / "no-frames-here")
    app = _run_camera()
    app.selectbox(key="cam_day").set_value(days[-1]).run()
    assert not app.exception
    text = _texts(app)
    assert "Kadr fayli topilmadi" in text and not app.get("image")
    assert "Oxirgi chiqish ko'rilmadi" not in text  # everybody's exit was seen on the first day


def test_page_hides_frames_when_not_allowed(frames_dir):
    _settings_env(frames_dir=frames_dir, show_frames="false")
    app = _run_camera()
    assert not app.get("image")
    assert "Kadrlar faqat ruxsatli rollarga" in _texts(app)


def test_page_without_camera_data():
    app = _run_camera({"p": "last_month"})  # the fixtures cover yesterday and today only
    text = _texts(app)
    assert "Bu davr uchun kamera hisobi" in text and not app.get("image") and not app.get("plotly_chart")


def test_timeline_marks_open_end_and_inferred():
    tz = load_settings().tz
    from ui import charts

    rows = [cam.from_row(_row(), tz), cam.from_row(_row(staff_no=3, last_out_at=None, frames=[]), tz)]
    now = datetime(2026, 10, 26, 20, 0, tzinfo=timezone.utc).astimezone(tz)
    fig = charts.staff_timeline(rows, now - timedelta(hours=1), now)
    names = {trace.name for trace in fig.data}
    assert {"Xonada", "Xonadan tashqarida", "Chiqish ko'rilmadi", "Taxminiy hodisa"} <= names
