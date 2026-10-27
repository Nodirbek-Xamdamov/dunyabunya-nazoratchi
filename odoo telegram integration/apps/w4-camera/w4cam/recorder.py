r"""W4-02: recording service. Channel 102 as is (ffmpeg -c copy, no decoding, no GPU) into 10-minute segments.

    cd apps\w4-camera
    python -m w4cam.recorder            (normally started by Task Scheduler, see scripts\register_recorder_task.ps1)

Layout: <video_root>\<camera_id>\<local YYYY-MM-DD>\<UTC start>Z.mkv + manifest.jsonl (see w4cam.manifest).
Time comes from the server clock (NTP), never from the camera. ffmpeg runs with TZ=UTC0, so its strftime
file names and its -segment_atclocktime cuts are in UTC (10-minute marks are the same in UTC+5).
Only inside the window (app.toml [recorder] window, local time): the segment is cut at the window end and
recording starts by itself at the window start.
Breaks: ffmpeg exits or the newest segment stops growing for stall_seconds -> ffmpeg is killed, the recorder
reconnects (1, 2, 4 ... backoff_max_seconds) and writes a "gap" line plus break_before on the next segment.
Heartbeat: <state_dir>\recorder_heartbeat.json. Graceful stop: create <state_dir>\STOP_RECORDER.
Logs: <paths.logs>\w4-recorder\w4-recorder.jsonl (RTSP address redacted).
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .manifest import Manifest, name_time, utc_iso
from .retention import purge
from .settings import RecorderSettings, load_settings, parse_window

log = logging.getLogger("w4-recorder")
FFMPEG = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg.exe"
GRACEFUL_STOP_S = 15
STDERR_LINES_PER_SESSION = 50
GAP_TOLERANCE_S = 3.0  # file names are whole seconds; a larger hole between segments is always a gap
CREATE_NO_WINDOW = 0x08000000


# ---------------------------------------------------------------- camera input

def camera_url(settings: RecorderSettings) -> str:
    from genesis_common import read_secret, register_secret

    line = next((x.strip() for x in read_secret(settings.secret).splitlines()
                 if x.strip() and not x.strip().startswith("#")), "")
    if not line.lower().startswith("rtsp://"):
        raise RuntimeError(f"secret {settings.secret}: not an rtsp:// address")
    if re.search(r"/Channels/\d+", line, re.I):
        url = re.sub(r"(/Channels/)\d+", rf"\g<1>{settings.channel}", line, flags=re.I)
    else:
        url = line.rstrip("/") + f"/Streaming/Channels/{settings.channel}"
    password = urllib.parse.urlsplit(url).password or ""
    for value in (url, password, urllib.parse.unquote(password)):
        register_secret(value)  # the log formatter replaces these with ***
    return url


def camera_input(settings: RecorderSettings) -> list[str]:
    return ["-rtsp_transport", "tcp", "-timeout", str(int(settings.socket_timeout_s * 1_000_000)),
            "-i", camera_url(settings), "-map", "0:v:0"]


# ---------------------------------------------------------------- Windows helpers

class KillOnCloseJob:
    """Windows job object: ffmpeg dies with the recorder even if the recorder is killed or crashes."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        k32 = ctypes.windll.kernel32

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32)]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = ctypes.c_void_p
        job = k32.CreateJobObjectW(None, None)
        info = Extended()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if job and k32.SetInformationJobObject(ctypes.c_void_p(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.handle = job

    def add(self, proc: subprocess.Popen) -> None:
        if self.handle:
            ctypes.windll.kernel32.AssignProcessToJobObject(ctypes.c_void_p(self.handle),
                                                            ctypes.c_void_p(int(proc._handle)))  # noqa: SLF001


class InstanceLock:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "a+b")  # noqa: SIM115 - held for the process lifetime
        try:
            if os.name == "nt":
                import msvcrt

                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.f.close()
            raise RuntimeError("another recorder is already running") from None


# ---------------------------------------------------------------- one ffmpeg run

class Session:
    """One ffmpeg process writing segments into one day folder."""

    def __init__(self, settings: RecorderSettings, input_args: list[str], folder: Path, manifest: Manifest,
                 pending_gap: str | None, job: KillOnCloseJob):
        self.s, self.folder, self.manifest, self.job = settings, folder, manifest, job
        self.id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.list_path = folder / f".segments_{self.id}.csv"
        self.cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostats", *input_args,
                    "-c", "copy", "-f", "segment", "-segment_time", str(settings.segment_s),
                    "-segment_atclocktime", "1", "-reset_timestamps", "1", "-strftime", "1",
                    "-segment_list", str(self.list_path), "-segment_list_type", "csv",
                    "-segment_format", "matroska",
                    # write every packet, 1 s clusters: without this the file stays at 0 bytes for ~7 s at 35 kbit/s,
                    # a kill loses that much and the growth check sees no growth (measured 2026-10-26)
                    "-segment_format_options", "flush_packets=1:cluster_time_limit=1000",
                    str(folder / "%Y%m%dT%H%M%SZ.mkv")]
        self.pending_gap = pending_gap  # reason of the break before this session, written with its 1st segment
        self.proc: subprocess.Popen | None = None
        self.list_pos = 0
        self.seq = 0
        self.anchor: datetime | None = None  # UTC time of media time 0 in this session
        self.last_t1: float | None = None  # media end of the last segment ffmpeg closed
        self.started_mono = 0.0
        self.last_size: tuple[str, int] | None = None
        self.last_growth_mono = 0.0
        self.last_growth_utc: datetime | None = None
        self.stderr_lines = 0

    # -- process
    def start(self) -> None:
        env = dict(os.environ, TZ="UTC0")
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                     env=env, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.job.add(self.proc)
        self.started_mono = self.last_growth_mono = time.monotonic()
        threading.Thread(target=self._pump, daemon=True).start()
        log.info("ffmpeg ishga tushdi", extra={"session": self.id, "ffmpeg_pid": self.proc.pid,
                                               "folder": str(self.folder)})

    def _pump(self) -> None:
        assert self.proc and self.proc.stderr
        for raw in self.proc.stderr:
            line = raw.strip()
            if line and self.stderr_lines < STDERR_LINES_PER_SESSION:
                self.stderr_lines += 1
                log.warning("ffmpeg: %s", line[:300], extra={"session": self.id})

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        """Ask ffmpeg to finish ('q'): it closes the current segment properly and writes its list line."""
        if not self.alive():
            return
        try:
            self.proc.stdin.write("q")
            self.proc.stdin.flush()
        except OSError:
            pass
        try:
            self.proc.wait(GRACEFUL_STOP_S)
        except subprocess.TimeoutExpired:
            self.kill()

    def kill(self) -> None:
        if self.alive():
            self.proc.kill()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                pass

    def close_pipes(self) -> None:
        for pipe in (self.proc.stdin, self.proc.stderr) if self.proc else ():
            try:
                pipe.close()
            except OSError:
                pass

    # -- files
    def poll_list(self) -> None:
        """Turn finished segments (ffmpeg's segment list) into manifest lines."""
        if not self.list_path.is_file():
            return
        with self.list_path.open("rb") as f:
            f.seek(self.list_pos)
            data = f.read()
        while b"\n" in data:
            line, data = data.split(b"\n", 1)
            self.list_pos += len(line) + 1
            parts = line.decode("utf-8", "replace").strip().split(",")
            if len(parts) < 3 or parts[0] in self.manifest.files:
                continue
            name, t0, t1 = os.path.basename(parts[0]), float(parts[1]), float(parts[2])
            opened = name_time(name)
            if opened is None:
                continue
            if self.anchor is None:
                self.anchor = opened - timedelta(seconds=t0)
            path = self.folder / name
            self._record(name, self.anchor + timedelta(seconds=t0), self.anchor + timedelta(seconds=t1),
                         path.stat().st_size if path.exists() else 0, "list", "segment")
            self.last_t1 = t1

    def finalize(self, closed: str) -> None:
        """After ffmpeg ended: list lines it still wrote, then files it never closed (end = last write)."""
        self.poll_list()
        for path in sorted(self.folder.glob("*.mkv")):
            if path.name in self.manifest.files:
                continue
            size = path.stat().st_size
            if size == 0:
                path.unlink(missing_ok=True)
                continue
            start = name_time(path.name)
            if start is None:
                continue
            if self.anchor is not None and self.last_t1 is not None:
                # the segment after the last closed one starts where that one ended (same media clock);
                # the file name is only whole seconds (measured: -0.95 s overlap without this)
                follow = self.anchor + timedelta(seconds=self.last_t1)
                if abs((follow - start).total_seconds()) < 3:
                    start = follow
                self.last_t1 = None
            end = max(start, datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
            self._record(path.name, start, end, size, "mtime", closed)

    def _record(self, name: str, start: datetime, end: datetime, size: int, source: str, closed: str) -> None:
        break_before = False
        # a break before this session: the hole starts at the last segment, or at the window start
        # when nothing was recorded yet today (the camera was down at 07:00)
        hole_from = self.manifest.last_end or self.manifest.window_start
        reason = self.pending_gap
        if not reason and self.manifest.last_end and (start - self.manifest.last_end).total_seconds() > GAP_TOLERANCE_S:
            # a hole nobody announced (the recorder that saw it was killed): still a gap
            reason = "recorder_restart" if closed == "recovered" else "unknown"
        if reason and hole_from and start > hole_from:
            self.manifest.add_gap(hole_from, start, reason)
            log.warning("bo'shliq belgilandi", extra={"from": utc_iso(hole_from), "to": utc_iso(start),
                                                      "reason": reason})
            break_before = True
        self.pending_gap = None
        rec = self.manifest.add_segment(name, start, end, size, self.id, self.seq, break_before, source, closed)
        self.seq += 1
        opened = name_time(name)
        log.info("bo'lak yopildi", extra={"file": name, "duration_s": rec["duration_s"], "bytes": size,
                                          "closed": closed, "end_source": source,
                                          "name_skew_s": round((start - opened).total_seconds(), 2) if opened else None})

    def newest(self) -> tuple[str, int] | None:
        files = [p for p in self.folder.glob("*.mkv") if p.name not in self.manifest.files]
        if not files:
            return None
        p = max(files, key=lambda x: x.name)
        return p.name, p.stat().st_size

    def stalled(self) -> bool:
        cur = self.newest()
        now = time.monotonic()
        if cur != self.last_size and cur is not None:
            self.last_size, self.last_growth_mono = cur, now
            self.last_growth_utc = datetime.now(timezone.utc)
        return now - self.last_growth_mono > self.s.stall_s


# ---------------------------------------------------------------- the service

class Recorder:
    def __init__(self, settings: RecorderSettings, input_args=None):
        self.s = settings
        self.input_args = input_args or (lambda: camera_input(settings))
        self.stop_event = threading.Event()
        self.job = KillOnCloseJob()
        self.session: Session | None = None
        self.manifest: Manifest | None = None
        self.pending_gap: str | None = None
        self.backoff = 1.0
        self.next_try_mono = 0.0
        self.state = "starting"
        self.next_retention_mono = 0.0
        self.next_disk_mono = 0.0
        self.next_heartbeat_mono = 0.0
        self.disk_free_gb: float | None = None
        self.low_disk_warned_mono = -1e9
        self.stop_file = settings.state_dir / "STOP_RECORDER"

    # -- time
    def now_local(self) -> datetime:
        return datetime.now(timezone.utc).astimezone(self.s.tz)

    def override(self) -> tuple[int, int] | None:
        """Temporary window for a test: <state_dir>/window_override.json {"window": "22:03-22:06",
        "until": "2026-10-26T22:08:00+05:00"}. Ignored and deleted once `until` has passed, so the real
        setting comes back by itself even if whoever wrote it never cleans up."""
        path = self.s.state_dir / "window_override.json"
        if not path.is_file():
            self._override_logged = None
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            until = datetime.fromisoformat(data["until"])
            win = parse_window(data["window"])
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.error("window_override.json yaroqsiz, e'tiborsiz", extra={"error": str(e)})
            return None
        if datetime.now(timezone.utc) >= until:
            path.unlink(missing_ok=True)
            log.info("vaqtinchalik oyna muddati tugadi, haqiqiy sozlama", extra={"window": data["window"]})
            self._override_logged = None
            return None
        if getattr(self, "_override_logged", None) != data["window"]:
            log.warning("vaqtinchalik oyna", extra={"window": data["window"], "until": data["until"]})
            self._override_logged = data["window"]
        return win

    def window(self, day) -> tuple[datetime, datetime]:
        midnight = datetime(day.year, day.month, day.day, tzinfo=self.s.tz)
        start_min, end_min = self.override() or (self.s.window_start_min, self.s.window_end_min)
        return midnight + timedelta(minutes=start_min), midnight + timedelta(minutes=end_min)

    def next_start(self, now: datetime) -> datetime:
        start, _ = self.window(now.date())
        return start if now < start else self.window(now.date() + timedelta(days=1))[0]

    # -- helpers
    def day_manifest(self, day: str, window_start: datetime) -> Manifest:
        if self.manifest is None or self.manifest.day != day:
            folder = self.s.camera_dir / day
            folder.mkdir(parents=True, exist_ok=True)
            self.manifest = Manifest(folder / "manifest.jsonl", self.s.camera_id, day)
            self.manifest.window_start = window_start
        return self.manifest

    def recover(self, manifest: Manifest) -> None:
        """Segments a previous run left unclosed (recorder killed, reboot): record them by file time."""
        folder = manifest.path.parent
        last = None
        for csv in sorted(folder.glob(".segments_*.csv")):  # lines ffmpeg wrote but we did not read yet
            last = Session(self.s, [], folder, manifest, None, self.job)
            last.id, last.list_path = csv.stem.replace(".segments_", ""), csv
            last.poll_list()
        # unclosed files belong to the newest session: its media clock continues them (no overlap)
        sess = last or Session(self.s, [], folder, manifest, None, self.job)
        sess.id = "recovered"
        sess.finalize("recovered")

    def end_session(self, closed: str, gap_reason: str | None) -> None:
        sess = self.session
        if sess is None:
            return
        if closed == "stop":
            sess.stop()
        else:
            sess.kill()
        rc = sess.proc.returncode if sess.proc else None
        sess.close_pipes()
        sess.finalize(closed)
        log.info("ffmpeg tugadi", extra={"session": sess.id, "closed": closed, "returncode": rc,
                                         "segments": sess.seq})
        self.session = None
        # a break this session inherited but never closed (it wrote nothing) is still the same hole
        self.pending_gap = sess.pending_gap or gap_reason
        if gap_reason:
            self.next_try_mono = time.monotonic() + self.backoff
            log.warning("uzilish, qayta ulanish", extra={"reason": gap_reason, "retry_in_s": self.backoff})
            self.backoff = min(self.backoff * 2, self.s.backoff_max_s)

    def close_open_gap(self, until: datetime, reason_suffix: str) -> None:
        """A break that lasted until the window end (or a stop): write it so the hole is visible."""
        if self.pending_gap and self.manifest:
            hole_from = self.manifest.last_end or self.manifest.window_start
            if hole_from and until > hole_from:
                self.manifest.add_gap(hole_from, until, f"{self.pending_gap}{reason_suffix}")
        self.pending_gap = None

    def check_disk(self) -> None:
        try:
            self.disk_free_gb = shutil.disk_usage(self.s.video_root.anchor or self.s.video_root).free / 2**30
        except OSError as e:
            log.error("disk o'qilmadi", extra={"error": str(e)})
            return
        if self.disk_free_gb < self.s.warn_free_gb and time.monotonic() - self.low_disk_warned_mono > 3600:
            log.warning("diskda joy kam", extra={"free_gb": round(self.disk_free_gb, 1),
                                                 "warn_below_gb": self.s.warn_free_gb})
            self.low_disk_warned_mono = time.monotonic()

    def heartbeat(self, now: datetime) -> None:
        sess = self.session
        cur = sess.newest() if sess else None
        data = {
            "ts_utc": utc_iso(datetime.now(timezone.utc)), "pid": os.getpid(), "state": self.state,
            "camera": self.s.camera_id, "channel": self.s.channel,
            "window": f"{self.s.window_start_min // 60:02d}:{self.s.window_start_min % 60:02d}-"
                      f"{self.s.window_end_min // 60:02d}:{self.s.window_end_min % 60:02d}",
            "day": now.date().isoformat(), "session": sess.id if sess else None,
            "ffmpeg_pid": sess.proc.pid if sess and sess.alive() else None,
            "current_file": cur[0] if cur else None, "current_bytes": cur[1] if cur else None,
            "last_growth_utc": utc_iso(sess.last_growth_utc) if sess and sess.last_growth_utc else None,
            "segments_today": len(self.manifest.files) if self.manifest and self.manifest.day == now.date().isoformat() else 0,
            "pending_gap": self.pending_gap, "disk_free_gb": round(self.disk_free_gb, 1) if self.disk_free_gb else None,
            "next_start_local": self.next_start(now).isoformat(timespec="seconds"),
        }
        path = self.s.state_dir / "recorder_heartbeat.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    # -- main loop
    def tick(self) -> None:
        now = self.now_local()
        mono = time.monotonic()
        day = now.date().isoformat()
        start, end = self.window(now.date())
        inside = start <= now < end

        if mono >= self.next_retention_mono:
            self.next_retention_mono = mono + 3600
            try:
                purge(self.s.camera_dir, now.date(), self.s.retention_days, self.s.keep_root)
            except OSError as e:
                log.error("bufer tozalashda xato", extra={"error": str(e)})
        if mono >= self.next_disk_mono:
            self.next_disk_mono = mono + 60
            self.check_disk()

        if self.session and (not inside or self.session.folder.name != day):
            self.end_session("stop", None)
            self.close_open_gap(min(now, end), "")
            log.info("yozish oynasi tugadi", extra={"day": day, "segments": len(self.manifest.files)})
            self.backoff = 1.0
        if not inside:
            if self.pending_gap:
                self.close_open_gap(end if now >= end else now, "")
            self.state = "idle"
            return

        if self.disk_free_gb is not None and self.disk_free_gb < self.s.stop_free_gb:
            if self.session:
                self.end_session("low_disk", "low_disk")
            self.state = "low_disk"
            return

        if self.session is None:
            manifest = self.day_manifest(day, start)
            if self.state in ("starting", "idle"):
                self.recover(manifest)
                if manifest.last_end and self.state == "starting":
                    self.pending_gap = "recorder_restart"  # the day already has segments: this is a hole
            if mono < self.next_try_mono:
                self.state = "waiting_retry"
                return
            try:
                args = self.input_args()
            except Exception as e:  # noqa: BLE001 - secret missing etc.: retry later, do not crash
                log.error("kamera manzili o'qilmadi", extra={"error": str(e)})
                self.next_try_mono = mono + self.s.backoff_max_s
                self.state = "waiting_retry"
                return
            self.session = Session(self.s, args, manifest.path.parent, manifest, self.pending_gap, self.job)
            self.pending_gap = None
            self.session.start()
            self.state = "recording"
            return

        sess = self.session
        sess.poll_list()
        if not sess.alive():
            self.end_session("ffmpeg_exit", "ffmpeg_exit")
        elif sess.stalled():
            log.warning("bo'lak o'smayapti, ffmpeg o'ldiriladi", extra={"stall_s": self.s.stall_s,
                                                                         "file": (sess.newest() or ("-",))[0]})
            self.end_session("stall", "stall")
        elif sess.seq > 0 and mono - sess.started_mono > 60:
            self.backoff = 1.0

    def run(self) -> int:
        self.s.state_dir.mkdir(parents=True, exist_ok=True)
        if self.stop_file.exists():
            self.stop_file.unlink()
            log.info("eski STOP_RECORDER fayli o'chirildi")
        log.info("recorder ishga tushdi", extra={"camera": self.s.camera_id, "channel": self.s.channel,
                                                 "video_root": str(self.s.video_root), "segment_s": self.s.segment_s,
                                                 "window_min": [self.s.window_start_min, self.s.window_end_min],
                                                 "retention_days": self.s.retention_days, "tz": str(self.s.tz)})
        try:
            while not self.stop_event.is_set():
                if self.stop_file.exists():
                    log.info("STOP_RECORDER topildi")
                    break
                self.tick()
                if time.monotonic() >= self.next_heartbeat_mono:
                    self.next_heartbeat_mono = time.monotonic() + self.s.heartbeat_s
                    self.heartbeat(self.now_local())
                self.stop_event.wait(1.0)
        finally:
            if self.session:
                self.end_session("stop", None)
            self.close_open_gap(datetime.now(timezone.utc), "+stop")
            self.state = "stopped"
            try:
                self.heartbeat(self.now_local())
            except OSError:
                pass
            log.info("recorder to'xtadi")
        return 0


def main() -> int:
    from genesis_common import load_config, setup_logging

    from .settings import APP

    setup_logging("w4-recorder", load_config(APP))
    settings = load_settings()
    try:
        _lock = InstanceLock(settings.state_dir / "recorder.lock")  # noqa: F841 - held until exit
    except RuntimeError as e:
        log.error(str(e))
        return 3
    rec = Recorder(settings)
    import signal

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: rec.stop_event.set())
    return rec.run()


if __name__ == "__main__":
    sys.exit(main())
