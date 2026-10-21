"""Camera service: live stream -> people -> desks -> intervals in camera.db, plus the newest frame.

    cd camera_service
    .venv\\Scripts\\python.exe -m camsvc.service

Writes: data\\camera.db (cam_* tables), data\\live\\latest.jpg (annotated) and latest_raw.jpg (clean,
for drawing zones), both replaced in place - no frame history. Logs: logs\\service.log (5 x 5 MB).
Stops cleanly on Ctrl+C or when the file data\\STOP exists (it is removed on the next start).
Settings, desks and employees are re-read when the dashboard changes them.
"""

from __future__ import annotations

import logging
import logging.handlers
import signal
import sys
import time
from zoneinfo import ZoneInfo

from camsvc import runtime  # noqa: F401  (must come before ultralytics)
from camsvc import db
from camsvc.config import LOGS_DIR, camera_from_env, mask
from camsvc.pipeline import Pipeline, draw, write_live
from camsvc.stream import LatestFrameReader

STOP_FILE = runtime.SERVICE_DIR / "data" / "STOP"
VERSION = "0.1"
log = logging.getLogger("camsvc")


class _MaskFilter(logging.Filter):
    def __init__(self, camera) -> None:
        super().__init__()
        self.camera = camera

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask(record.getMessage(), self.camera)
        record.args = ()
        return True


def setup_logging(camera) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOGS_DIR / "service.log", maxBytes=5 * 2**20, backupCount=5,
                                                   encoding="utf-8")
    console = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (handler, console):
        h.setFormatter(fmt)
        h.addFilter(_MaskFilter(camera))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [handler, console]
    logging.getLogger("ultralytics").setLevel(logging.WARNING)


class Coverage:
    """Per minute: frames we wanted (target_fps x seconds running), frames processed, camera frames."""

    def __init__(self, conn, source: str = "live"):
        self.conn, self.source = conn, source
        self.minute = None
        self.prev: float | None = None
        self.expected = self.processed = self.camera = 0.0
        self.connected_s = 0.0

    def tick(self, now: float, period: float, fps: float, processed: bool, camera_frames: int, connected: bool):
        # real elapsed time, capped: if the loop itself stalls, those minutes simply have no row (= no data)
        elapsed = period if self.prev is None else min(now - self.prev, 2 * period)
        self.prev = now
        minute = int(now // 60) * 60
        if self.minute is not None and minute != self.minute:
            self.flush()
        self.minute = minute
        self.expected += elapsed * fps
        self.processed += int(processed)
        self.camera += camera_frames
        self.connected_s += elapsed if connected else 0.0

    def flush(self) -> None:
        if self.minute is None:
            return
        db.add_coverage(self.conn, self.source, self.minute, round(self.expected), int(self.processed), int(self.camera),
                        round(self.connected_s, 1))
        self.expected = self.processed = self.camera = self.connected_s = 0.0


def run() -> int:
    camera = camera_from_env()
    setup_logging(camera)
    runtime.check()
    import torch

    STOP_FILE.unlink(missing_ok=True)
    conn = db.connect()
    db.init(conn)
    settings = db.settings(conn)
    tz = ZoneInfo(settings.get("timezone", "Asia/Tashkent"))
    stamp = db.settings_stamp(conn)
    log.info("start: version %s, channel %s, model %s, imgsz %s, %s kadr/s", VERSION, settings["channel"],
             settings["model"], settings["imgsz"], settings["target_fps"])
    db.event(conn, "started", f"xizmat ishga tushdi (kanal {settings['channel']}, model {settings['model']})")

    channel = {"value": int(settings["channel"])}
    reader = LatestFrameReader(lambda: camera.url(channel["value"]), stall_seconds=float(settings["stall_s"]),
                               log=log.info, name="kamera").start()
    pipeline = Pipeline(conn, settings, db.active_desks(conn))
    pipeline.resume_open(time.time())
    coverage = Coverage(conn)

    running = {"on": True}

    def _stop(*_args):
        running["on"] = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _stop)

    last_seq = 0
    last_frames = 0
    last_draw = last_status = last_check = 0.0
    recent: list[float] = []
    fps = float(settings["target_fps"])
    period = 1.0 / fps
    next_tick = time.monotonic()
    was_connected = False
    try:
        while running["on"]:
            frame = reader.wait_newer(last_seq, period * 0.9)
            now = time.time()
            processed = False
            if frame is not None and frame.seq > last_seq:
                last_seq = frame.seq
                pipeline.process(frame.image, frame.wall)
                processed = True
                recent.append(now)
                if now - last_draw >= 1.0:
                    last_draw = now
                    write_live(frame.image, draw(frame.image, pipeline, frame.wall, tz), frame.wall)
            else:
                pipeline.idle(now)
            frames_now = reader.stats.frames
            coverage.tick(now, period, fps, processed, frames_now - last_frames, reader.stats.connected)
            last_frames = frames_now
            if reader.stats.connected != was_connected:
                was_connected = reader.stats.connected
                db.event(conn, "connected" if was_connected else "disconnected",
                         "kamera ulandi" if was_connected else f"kamera uzildi: {reader.stats.last_error}")
            if now - last_status >= 5:
                last_status = now
                recent = [r for r in recent if r > now - 10]
                db.set_status(conn, {
                    "heartbeat": now, "version": VERSION, "camera_connected": reader.stats.connected,
                    "camera_fps": reader.camera_fps, "processed_fps": round(len(recent) / 10, 2),
                    "last_frame_ts": reader.stats.last_frame_wall, "frame_size": reader.frame_size,
                    "infer_ms": round((pipeline.last or {}).get("infer_ms", 0), 1),
                    "gpu_mem_mb": round(torch.cuda.memory_reserved() / 2**20), "model": settings["model"],
                    "imgsz": settings["imgsz"], "channel": channel["value"], "target_fps": fps,
                    "reconnects": reader.stats.disconnects, "desks": pipeline.desk_states(now)})
            if now - last_check >= 10:
                last_check = now
                if STOP_FILE.exists():
                    log.info("STOP fayli topildi")
                    break
                new_stamp = db.settings_stamp(conn)
                if new_stamp != stamp:
                    stamp = new_stamp
                    settings = db.settings(conn)
                    fps = float(settings["target_fps"])
                    period = 1.0 / fps
                    log.info("sozlamalar o'zgardi: qayta yuklanmoqda")
                    db.event(conn, "info", "sozlamalar yoki stollar o'zgardi, qayta yuklandi")
                    if int(settings["channel"]) != channel["value"]:
                        channel["value"] = int(settings["channel"])
                        reader.drop_connection()
                    pipeline = Pipeline(conn, settings, db.active_desks(conn))
                    pipeline.resume_open(time.time())
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -period:
                next_tick = time.monotonic()
    except Exception:
        log.exception("xizmatda xato")
        db.event(conn, "error", "xizmatda xato, log: logs\\service.log")
        raise
    finally:
        coverage.flush()
        pipeline.stop(close=False)
        reader.stop()
        db.event(conn, "stopped", "xizmat to'xtadi")
        log.info("to'xtadi")
    return 0


if __name__ == "__main__":
    sys.exit(run())
