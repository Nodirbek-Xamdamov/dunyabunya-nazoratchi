"""5-second arrival/departure videos: which frames go in, when they are saved, and that every row carries the
desk's fixed employee id (tracker IDs never reach the database). A fake model stands in for YOLO."""

import sys
import types

import numpy as np
import pytest

from camsvc import db
from camsvc.clips import AFTER_OUT, ClipRecorder

FPS = 5
STEP = 1 / FPS
ZONE = [[0.5, 0.5], [0.9, 0.5], [0.9, 0.9], [0.5, 0.9]]
SETTINGS = {**db.DEFAULT_SETTINGS, "away_grace_s": 60.0, "roi_margin": None}


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "camera.db")
    db.init(c)
    db.seed(c, [("Uzoq stol", "Nodir")])
    with c:
        c.execute("UPDATE cam_desks SET zone = ?", (str(ZONE).replace("'", '"'),))
    yield c
    c.close()


def frame(value: int = 0) -> np.ndarray:
    image = np.zeros((360, 640, 3), np.uint8)
    image[:, :, 0] = value % 256
    return image


def recorder(conn, tmp_path) -> ClipRecorder:
    return ClipRecorder(conn, "live", clips_dir=tmp_path / "clips", ffmpeg=None)


def test_arrival_takes_three_seconds_before_and_two_after(conn, tmp_path):
    rec = recorder(conn, tmp_path)
    for i in range(100):  # 20 s
        rec.add(1000 + i * STEP, frame(i))
    rec.arrival(1, 1, 7, 1010.0)
    rec.close()
    row = conn.execute("SELECT * FROM cam_clips").fetchone()
    assert (row["kind"], row["employee_id"], row["interval_id"], row["event_ts"]) == ("in", 1, 7, 1010.0)
    assert row["path"].startswith("live/") and row["path"].endswith("_in.mp4")
    assert (tmp_path / "clips" / row["path"]).with_suffix(".jpg").exists()  # no ffmpeg: the middle frame
    assert len(rec._between(1007.0, 1012.0)) == 26


def test_departure_uses_frames_put_aside_at_the_last_sighting(conn, tmp_path):
    rec = recorder(conn, tmp_path)
    t = 1000.0
    for _ in range(int(AFTER_OUT * FPS) + 1):
        t += STEP
        rec.add(t, frame())
        rec.watch(1, 1000.0, t)
    kept = rec._pending[1]
    assert kept[0] == 1000.0 and int(AFTER_OUT * FPS) - 1 <= len(kept[1]) <= int(AFTER_OUT * FPS)
    for _ in range(600):  # two more minutes: the buffer no longer holds the last sighting
        t += STEP
        rec.add(t, frame())
        rec.watch(1, 1000.0, t)
    assert rec._between(998.0, 1003.0) == []
    rec.departure(1, 1, 7, 1000.0)
    rec.close()
    assert [tuple(r) for r in conn.execute("SELECT kind, event_ts FROM cam_clips")] == [("out", 1000.0)]


def test_cleanup_deletes_only_old_live_videos(conn, tmp_path):
    rec = recorder(conn, tmp_path)
    for ts in (1000.0, 1000.0 + 40 * 86400):
        rec.add(ts, frame())
        rec.arrival(1, 1, 1, ts)
    rec.close()
    assert rec.cleanup(30, now=1000.0 + 40 * 86400) == 1
    assert [r["event_ts"] for r in conn.execute("SELECT event_ts FROM cam_clips")] == [1000.0 + 40 * 86400]


# --------------------------------------------------------------------------- the pipeline with a fake model


class _Values(list):
    def tolist(self):
        return list(self)

    def int(self):
        return self


class _Boxes:
    def __init__(self, boxes):
        self.xyxy = _Values(b[0] for b in boxes)
        self.conf = _Values(b[1] for b in boxes)
        self.id = _Values(b[2] for b in boxes)

    def __len__(self):
        return len(self.xyxy)


class FakeYOLO:
    script: list = []  # per call: list of (box, conf, track id)

    def __init__(self, _path):
        self.calls = 0

    def track(self, *_args, **_kwargs):
        boxes = FakeYOLO.script[self.calls] if self.calls < len(FakeYOLO.script) else []
        self.calls += 1
        return [types.SimpleNamespace(boxes=_Boxes(boxes))]


@pytest.fixture()
def fake_yolo(monkeypatch):
    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=FakeYOLO))
    return FakeYOLO


def test_one_employee_one_id_whatever_the_tracker_says(conn, tmp_path, fake_yolo):
    from camsvc.pipeline import Pipeline

    seated = (400.0, 220.0, 520.0, 330.0)  # anchor (460, 275) is inside the zone of a 640x360 frame
    # sits 30 s with a new tracker ID every 5 frames, away 70 s (> grace 60 s), back for 30 s with other IDs
    fake_yolo.script = ([[(seated, 0.9, 100 + i // 5)] for i in range(150)] + [[] for _ in range(350)]
                        + [[(seated, 0.9, 900 + i)] for i in range(150)] + [[] for _ in range(350)])
    rec = recorder(conn, tmp_path)
    pipeline = Pipeline(conn, SETTINGS, db.active_desks(conn), recorder=rec)
    t = 1000.0
    for _ in range(len(fake_yolo.script)):
        t += STEP
        pipeline.process(frame(), t)
    rec.close()
    intervals = conn.execute("SELECT employee_id, close_reason FROM cam_intervals ORDER BY start_ts").fetchall()
    assert [tuple(r) for r in intervals] == [(1, "away"), (1, "away")]
    clips = conn.execute("SELECT employee_id, kind FROM cam_clips ORDER BY event_ts").fetchall()
    assert [tuple(r) for r in clips] == [(1, "in"), (1, "out"), (1, "in"), (1, "out")]
    assert conn.execute("SELECT COUNT(*) FROM cam_employees").fetchone()[0] == 1
