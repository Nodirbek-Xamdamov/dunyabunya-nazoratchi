"""W4-02 recorder tests (stdlib unittest). No camera: a synthetic HEVC file is copied like the camera stream.

    cd apps\w4-camera
    python -m unittest tests.test_recorder -v
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from w4cam import retention  # noqa: E402
from w4cam.frames import cut, find_segment  # noqa: E402
from w4cam.manifest import Manifest, name_time, parse_utc  # noqa: E402
from w4cam.recorder import FFMPEG, Recorder, Session, KillOnCloseJob  # noqa: E402
from w4cam.settings import RecorderSettings, parse_offset, parse_window  # noqa: E402

TZ = parse_offset("+05:00")


def settings(root: Path, **kw) -> RecorderSettings:
    base = dict(camera_id="camtest", secret="none", channel=102, window_start_min=0, window_end_min=1440, tz=TZ,
                segment_s=10, video_root=root / "video", keep_root=root / "keep", retention_days=14, stall_s=60,
                socket_timeout_s=5, backoff_max_s=4, warn_free_gb=0, stop_free_gb=0, heartbeat_s=1,
                state_dir=root / "state")
    base.update(kw)
    return RecorderSettings(**base)


def records(manifest: Path, kind: str) -> list[dict]:
    if not manifest.is_file():
        return []
    return [r for r in (json.loads(x) for x in manifest.read_text(encoding="utf-8").splitlines()) if r["type"] == kind]


class Pure(unittest.TestCase):
    def test_window(self):
        self.assertEqual(parse_window("07:00-22:00"), (420, 1320))
        with self.assertRaises(ValueError):
            parse_window("22:00-07:00")

    def test_name_time(self):
        self.assertEqual(name_time("20261027T020002Z.mkv"), datetime(2026, 10, 27, 2, 0, 2, tzinfo=timezone.utc))
        self.assertIsNone(name_time("manifest.jsonl"))

    def test_manifest_gap_and_reload(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "2026-10-27" / "manifest.jsonl"
            m = Manifest(p, "c", "2026-10-27")
            t = datetime(2026, 10, 27, 2, 0, 2, tzinfo=timezone.utc)
            m.add_segment("a.mkv", t, t + timedelta(seconds=598), 100, "s1", 0, False, "list", "segment")
            m.add_gap(t + timedelta(seconds=598), t + timedelta(seconds=610), "stall")
            r = m.add_segment("b.mkv", t + timedelta(seconds=610), t + timedelta(seconds=900), 50, "s2", 0, True,
                              "mtime", "stop")
            self.assertEqual(r["gap_before_s"], 12.0)
            with p.open("a", encoding="utf-8") as f:
                f.write('{"torn')  # a crash in the middle of a line
            m2 = Manifest(p, "c", "2026-10-27")
            self.assertEqual(m2.files, {"a.mkv", "b.mkv"})
            self.assertEqual(m2.last_end, t + timedelta(seconds=900))

    def test_retention(self):
        with tempfile.TemporaryDirectory() as d:
            cam = Path(d) / "video" / "c"
            for day in ("2026-10-01", "2026-10-12", "2026-10-13", "2026-10-26", "notes"):
                (cam / day).mkdir(parents=True)
                (cam / day / "x.mkv").write_bytes(b"x")
            keep = Path(d) / "keep" / "c" / "2026-10-01"
            keep.mkdir(parents=True)
            removed = retention.purge(cam, date(2026, 10, 26), 14, Path(d) / "keep")
            self.assertEqual(removed, ["2026-10-01", "2026-10-12"])  # 14 days kept: 10-13 .. 10-26
            self.assertTrue((cam / "2026-10-13").is_dir() and (cam / "notes").is_dir() and keep.is_dir())

    def test_recover_reads_lists_then_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            s = settings(Path(d))
            folder = s.camera_dir / "2026-10-27"
            folder.mkdir(parents=True)
            (folder / "20261027T020002Z.mkv").write_bytes(b"a" * 10)
            (folder / "20261027T021001Z.mkv").write_bytes(b"b" * 10)  # name 1 s late: start must follow the list
            (folder / "20261027T022000Z.mkv").write_bytes(b"")  # empty: dropped
            (folder / ".segments_20261027T020000Z.csv").write_text(
                "20261027T020002Z.mkv,1.000000,599.000000\n", encoding="utf-8")
            m = Manifest(folder / "manifest.jsonl", s.camera_id, "2026-10-27")
            Recorder(s, input_args=lambda: []).recover(m)
            segs = records(folder / "manifest.jsonl", "segment")
            self.assertEqual([x["file"] for x in segs], ["20261027T020002Z.mkv", "20261027T021001Z.mkv"])
            self.assertEqual([x["end_source"] for x in segs], ["list", "mtime"])
            self.assertEqual(segs[0]["start_utc"], "2026-10-27T02:00:02.000Z")
            self.assertAlmostEqual(segs[0]["duration_s"], 598.0)
            self.assertEqual(segs[1]["start_utc"], segs[0]["end_utc"])  # continues, no overlap
            self.assertFalse((folder / "20261027T022000Z.mkv").exists())


class UnannouncedHole(unittest.TestCase):
    def test_recovered_segment_after_a_hole_gets_a_gap(self):
        """The recorder that saw the break was killed before writing it: the next one still marks it."""
        with tempfile.TemporaryDirectory() as d:
            s = settings(Path(d))
            folder = s.camera_dir / "2026-10-27"
            folder.mkdir(parents=True)
            m = Manifest(folder / "manifest.jsonl", s.camera_id, "2026-10-27")
            t = datetime(2026, 10, 27, 2, 0, 0, tzinfo=timezone.utc)
            m.add_segment("20261027T020000Z.mkv", t, t + timedelta(seconds=60), 10, "s1", 0, False, "list", "segment")
            (folder / "20261027T020600Z.mkv").write_bytes(b"x" * 10)  # 5 min later, never closed
            Recorder(s, input_args=lambda: []).recover(m)
            gaps = records(folder / "manifest.jsonl", "gap")
            segs = records(folder / "manifest.jsonl", "segment")
            self.assertEqual(len(gaps), 1, gaps)
            self.assertEqual(gaps[0]["reason"], "recorder_restart")
            self.assertEqual(gaps[0]["from_utc"], "2026-10-27T02:01:00.000Z")
            self.assertTrue(segs[-1]["break_before"])


class Override(unittest.TestCase):
    def test_override_applies_then_expires(self):
        with tempfile.TemporaryDirectory() as d:
            s = settings(Path(d), window_start_min=420, window_end_min=1320)
            s.state_dir.mkdir(parents=True)
            rec = Recorder(s, input_args=lambda: [])
            day = date(2026, 10, 26)
            self.assertEqual(rec.window(day)[1].strftime("%H:%M"), "22:00")
            f = s.state_dir / "window_override.json"
            f.write_text(json.dumps({"window": "22:03-22:06",
                                     "until": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}))
            self.assertEqual([t.strftime("%H:%M") for t in rec.window(day)], ["22:03", "22:06"])
            f.write_text(json.dumps({"window": "22:03-22:06",
                                     "until": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}))
            self.assertEqual([t.strftime("%H:%M") for t in rec.window(day)], ["07:00", "22:00"])
            self.assertFalse(f.exists(), "expired override is removed")
            f.write_text("{broken")
            self.assertEqual(rec.window(day)[0].strftime("%H:%M"), "07:00")


class FakeWindow(Recorder):
    """Recorder with a window in seconds (accelerated 07:00 / 22:00 test)."""

    def __init__(self, s, input_args, start, end):
        super().__init__(s, input_args)
        self._w = (start, end)

    def window(self, day):
        return self._w


@unittest.skipUnless(os.environ.get("W4_SLOW", "1") == "1", "slow ffmpeg tests")
class WithFfmpeg(unittest.TestCase):
    tmp: Path
    sample: Path

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="w4rec_"))
        cls.sample = cls.tmp / "sample.mkv"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=640x360:rate=20", "-t", "30", "-c:v", "libx265", "-preset", "ultrafast",
                        "-x265-params", "keyint=50:bframes=0:log-level=none", "-y", str(cls.sample)], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def source(self):
        return ["-re", "-stream_loop", "-1", "-i", str(self.sample), "-map", "0:v:0"]

    def run_recorder(self, rec: Recorder, seconds: float, during=None):
        t = threading.Thread(target=rec.run, daemon=True)
        t.start()
        try:
            if during:
                during(rec)
            else:
                time.sleep(seconds)
        finally:
            rec.stop_event.set()
            t.join(40)
        self.assertFalse(t.is_alive())

    def test_window_start_and_end(self):
        root = self.tmp / "win"
        s = settings(root)
        now = datetime.now(timezone.utc).astimezone(TZ)
        start, end = now + timedelta(seconds=6), now + timedelta(seconds=32)
        rec = FakeWindow(s, self.source, start, end)
        seen = {}

        def during(r):
            time.sleep(3)
            seen["before"] = r.session is not None
            while datetime.now(timezone.utc) < end + timedelta(seconds=5):
                time.sleep(0.5)
            seen["after"] = r.session is not None

        self.run_recorder(rec, 0, during)
        m = s.camera_dir / now.date().isoformat() / "manifest.jsonl"
        segs, gaps = records(m, "segment"), records(m, "gap")
        self.assertFalse(seen["before"], "recorded before the window")
        self.assertFalse(seen["after"], "still recording after the window")
        self.assertGreaterEqual(len(segs), 3)
        self.assertEqual(gaps, [])
        first, last = parse_utc(segs[0]["start_utc"]), parse_utc(segs[-1]["end_utc"])
        self.assertLess(abs((first - start).total_seconds()), 4, segs[0])
        self.assertLess(abs((last - end).total_seconds()), 2.5, segs[-1])
        for a, b in zip(segs, segs[1:]):
            self.assertLess(abs((parse_utc(b["start_utc"]) - parse_utc(a["end_utc"])).total_seconds()), 0.2)
        self.assertTrue(all(name_time(x["file"]) for x in segs))  # UTC names
        self.assertTrue(all(x["end_source"] == "list" for x in segs), [x["end_source"] for x in segs])
        # a frame can be cut from the recording afterwards
        t = first + timedelta(seconds=12)
        found = find_segment(s, t.astimezone(TZ))
        self.assertIsNotNone(found)
        self.assertTrue(cut(found[0], found[1], root / "frame.jpg"))

    def test_kill_reconnects_and_marks_gap(self):
        s = settings(self.tmp / "kill")

        def during(r):
            time.sleep(14)
            r.session.proc.kill()
            time.sleep(16)

        rec = Recorder(s, self.source)
        self.run_recorder(rec, 0, during)
        day = datetime.now(timezone.utc).astimezone(TZ).date().isoformat()
        m = s.camera_dir / day / "manifest.jsonl"
        segs, gaps = records(m, "segment"), records(m, "gap")
        self.assertEqual(len(gaps), 1, gaps)
        self.assertEqual(gaps[0]["reason"], "ffmpeg_exit")
        self.assertLess(gaps[0]["duration_s"], 8)
        after = [x for x in segs if x["break_before"]]
        self.assertEqual(len(after), 1)
        self.assertEqual(len({x["session"] for x in segs}), 2)
        # inside one session the segments touch: no overlap, no hole (the killed one follows the last closed one)
        for a, b in zip(segs, segs[1:]):
            if a["session"] == b["session"]:
                self.assertAlmostEqual(parse_utc(b["start_utc"]).timestamp(), parse_utc(a["end_utc"]).timestamp(),
                                       delta=0.2, msg=(a, b))

    def test_stall_is_detected(self):
        s = settings(self.tmp / "stall", stall_s=4)

        def during(r):
            time.sleep(12)
            pid = r.session.proc.pid
            h = ctypes.windll.kernel32.OpenProcess(0x0800, False, pid)  # PROCESS_SUSPEND_RESUME
            ctypes.windll.ntdll.NtSuspendProcess(h)
            ctypes.windll.kernel32.CloseHandle(h)
            time.sleep(20)

        rec = Recorder(s, self.source)
        self.run_recorder(rec, 0, during)
        day = datetime.now(timezone.utc).astimezone(TZ).date().isoformat()
        gaps = records(s.camera_dir / day / "manifest.jsonl", "gap")
        self.assertTrue(any(g["reason"] == "stall" for g in gaps), gaps)

    def test_ffmpeg_dies_with_recorder(self):
        code = ("import sys,time,subprocess; sys.path.insert(0, r'%s');"
                "from w4cam.recorder import KillOnCloseJob, FFMPEG;"
                "j=KillOnCloseJob(); p=subprocess.Popen([FFMPEG,'-hide_banner','-loglevel','error','-re','-stream_loop',"
                "'-1','-i',r'%s','-f','null','-'], stdin=subprocess.DEVNULL); j.add(p); print(p.pid, flush=True);"
                "time.sleep(60)") % (APP_DIR, self.sample)
        parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        pid = int(parent.stdout.readline())
        parent.kill()
        parent.wait(10)
        parent.stdout.close()
        time.sleep(1.5)
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        self.assertNotIn(str(pid), out, "ffmpeg survived the recorder")


if __name__ == "__main__":
    unittest.main()
