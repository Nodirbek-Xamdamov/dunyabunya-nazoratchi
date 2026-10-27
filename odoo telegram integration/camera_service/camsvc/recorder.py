"""Camera raw data: the camera stream copied as is (no re-encoding) into 10-minute files, only inside the
daily recording window (07:00-23:00 for now).

    cd camera_service
    .venv\\Scripts\\python.exe -m camsvc.recorder

Files (since 2026-10-26, user's layout: year / month / camera / day):
    E:\\Kamera raw data\\2026\\10-Oktabr\\Koridor 2\\2026-10-26\\07-00-00.mkv, 07-10-00.mkv ... 22-50-00.mkv
Video only, ~650 MB per hour on channel 101. Settings in camera_service\\.env (all optional):
    RECORD_ROOT=E:\\Kamera raw data   RECORD_CAMERA_NAME=Koridor 2   RECORD_FROM=07:00   RECORD_TO=23:00
The window is wide on purpose: someone may come before 09:00 or stay late. Later the cleaning step keeps
only first arrival -> last departure.

ffmpeg does the copying; this process restarts it when the stream breaks or stops growing (1, 2, 4 ... 30 s),
stops it cleanly ("q", so the last file is closed properly) at the end of the window and when data\\STOP_REC
exists. With less than MIN_FREE_GB free on the disk it stops writing and waits - it never deletes anything.
Logs: logs\\recorder.log. The RTSP address holds the password, so every ffmpeg line goes through mask().
"""

from __future__ import annotations

import datetime as dt
import logging
import logging.handlers
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from camsvc.config import LOGS_DIR, SERVICE_DIR, Camera, camera_from_env, load_env, mask

FFMPEG = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg.exe"
CHANNEL = 101  # main stream, the same picture the analysis uses
SEGMENT_S = 600  # one file per 10 minutes, cut at :00, :10, :20 ... by the clock
SOCKET_TIMEOUT_US = "10000000"  # 10 s without data -> ffmpeg exits and is restarted
STALL_S = 60  # ffmpeg alive but the newest file has not grown for this long -> restart
MIN_FREE_GB = 50
STOP_FILE = SERVICE_DIR / "data" / "STOP_REC"
BACKOFF_START, BACKOFF_MAX = 1.0, 30.0
MONTHS = ("Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
          "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr")
log = logging.getLogger("recorder")


@dataclass(frozen=True)
class RecordConfig:
    root: Path
    camera_name: str
    start_min: int  # minutes after midnight
    end_min: int  # 1440 = midnight at the end of the day

    def window(self, day: dt.date) -> tuple[dt.datetime, dt.datetime]:
        midnight = dt.datetime.combine(day, dt.time())
        return midnight + dt.timedelta(minutes=self.start_min), midnight + dt.timedelta(minutes=self.end_min)

    def next_start(self, now: dt.datetime) -> dt.datetime:
        start = self.window(now.date())[0]
        return start if now < start else self.window(now.date() + dt.timedelta(days=1))[0]


def parse_hhmm(text: str, default: int) -> int:
    """"07:00" -> 420, "24:00" -> 1440; anything else -> default."""
    try:
        hours, minutes = (int(part) for part in text.strip().split(":"))
    except ValueError:
        return default
    total = hours * 60 + minutes
    return total if 0 <= minutes < 60 and 0 <= total <= 1440 else default


def record_config(values: dict[str, str] | None = None) -> RecordConfig:
    v = load_env() if values is None else values
    start = parse_hhmm(v.get("RECORD_FROM", ""), 7 * 60)
    end = parse_hhmm(v.get("RECORD_TO", ""), 23 * 60)
    if end <= start:  # the window must not cross midnight: one day = one folder
        start, end = 7 * 60, 23 * 60
    return RecordConfig(root=Path(v.get("RECORD_ROOT", "").strip() or r"E:\Kamera raw data"),
                        camera_name=v.get("RECORD_CAMERA_NAME", "").strip() or "Koridor 2",
                        start_min=start, end_min=end)


def day_dir(cfg: RecordConfig, day: dt.date) -> Path:
    """<root>\\2026\\10-Oktabr\\<camera>\\2026-10-26"""
    return cfg.root / f"{day:%Y}" / f"{day:%m}-{MONTHS[day.month - 1]}" / cfg.camera_name / day.isoformat()


def ffmpeg_cmd(camera: Camera, channel: int, folder: Path) -> list[str]:
    return [FFMPEG, "-hide_banner", "-loglevel", "warning",
            "-rtsp_transport", "tcp", "-timeout", SOCKET_TIMEOUT_US, "-i", camera.url(channel),
            "-map", "0:v:0", "-c", "copy",
            "-f", "segment", "-segment_time", str(SEGMENT_S), "-segment_atclocktime", "1",
            "-reset_timestamps", "1", "-strftime", "1", str(folder / "%H-%M-%S.mkv")]


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path.anchor or path).free / 2**30


def newest_file(folder: Path) -> tuple[str, int] | None:
    """The newest recording of the day and its size; names sort by clock time."""
    files = sorted(folder.glob("*.mkv")) if folder.is_dir() else []
    return (str(files[-1]), files[-1].stat().st_size) if files else None


def day_summary(folder: Path) -> str:
    files = list(folder.glob("*.mkv")) if folder.is_dir() else []
    return f"{len(files)} fayl, {sum(f.stat().st_size for f in files) / 2**30:.1f} GB"


def setup_logging(camera: Camera) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOGS_DIR / "recorder.log", maxBytes=5 * 2**20, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(handler.formatter)
    log.setLevel(logging.INFO)
    log.handlers[:] = [handler, console]
    log.info("kamera: %s", camera.masked_url(CHANNEL))


def _pump(proc: subprocess.Popen, camera: Camera) -> None:
    assert proc.stderr is not None
    for raw in proc.stderr:
        line = mask(raw.rstrip(), camera)
        if line:
            log.warning("ffmpeg: %s", line)


def _stop_ffmpeg(proc: subprocess.Popen) -> None:
    try:
        proc.stdin.write("q")
        proc.stdin.flush()
    except OSError:
        pass
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(5)


def run() -> int:
    camera = camera_from_env()
    cfg = record_config()
    setup_logging(camera)
    STOP_FILE.unlink(missing_ok=True)
    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: stop.set())
    log.info("start: kanal %s, %s daqiqalik fayllar, %02d:%02d-%02d:%02d, papka %s\\<yil>\\<oy>\\%s\\<kun>",
             CHANNEL, SEGMENT_S // 60, *divmod(cfg.start_min, 60), *divmod(cfg.end_min, 60), cfg.root, cfg.camera_name)

    backoff = BACKOFF_START
    low_disk_logged = waiting_logged = False
    while not stop.is_set() and not STOP_FILE.exists():
        now = dt.datetime.now()
        start, end = cfg.window(now.date())
        if not start <= now < end:
            if not waiting_logged:
                log.info("yozish vaqti emas, keyingisi %s", f"{cfg.next_start(now):%Y-%m-%d %H:%M}")
                waiting_logged = True
            stop.wait(15)
            continue
        waiting_logged = False
        folder = day_dir(cfg, now.date())
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # disk ulanmagan va h.k.
            log.error("papkani yaratib bo'lmadi (%s): %s", folder, exc)
            stop.wait(60)
            continue
        if free_gb(folder) < MIN_FREE_GB:
            if not low_disk_logged:
                log.error("diskda %.0f GB qoldi (< %s GB): yozish to'xtatildi, hech narsa o'chirilmaydi",
                          free_gb(folder), MIN_FREE_GB)
                low_disk_logged = True
            stop.wait(60)
            continue
        low_disk_logged = False
        started = time.monotonic()
        proc = subprocess.Popen(ffmpeg_cmd(camera, CHANNEL, folder), stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace")
        pump = threading.Thread(target=_pump, args=(proc, camera), daemon=True)
        pump.start()
        log.info("yozish boshlandi (ffmpeg pid %s) -> %s", proc.pid, folder)
        reason = ""
        seen, seen_at = newest_file(folder), time.monotonic()
        while proc.poll() is None:
            if stop.wait(5) or STOP_FILE.exists():
                reason = "to'xtatildi"
            elif dt.datetime.now() >= end:
                reason = "kun oynasi tugadi"
            else:
                current = newest_file(folder)
                if current != seen:
                    seen, seen_at = current, time.monotonic()
                elif time.monotonic() - seen_at > STALL_S:
                    reason = f"{STALL_S} s fayl o'smadi"
                elif free_gb(folder) < MIN_FREE_GB:
                    reason = "disk to'lyapti"
            if reason:
                _stop_ffmpeg(proc)
                break
        pump.join(timeout=5)
        if stop.is_set() or STOP_FILE.exists():
            break
        if reason == "kun oynasi tugadi":
            log.info("kun tugadi: %s (%s)", folder, day_summary(folder))
            backoff = BACKOFF_START
            continue
        if time.monotonic() - started > 300:
            backoff = BACKOFF_START
        log.warning("ffmpeg to'xtadi (%s, exit %s), %.0f s dan keyin qayta", reason or "oqim uzildi",
                    proc.returncode, backoff)
        stop.wait(backoff)
        backoff = min(backoff * 2, BACKOFF_MAX)
    log.info("to'xtadi")
    return 0


if __name__ == "__main__":
    sys.exit(run())
