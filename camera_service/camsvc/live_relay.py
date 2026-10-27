"""Live view relay: camera -> dashboard directly, only while someone watches, independent of the analysis.

    cd camera_service
    .venv\\Scripts\\python.exe -m camsvc.live_relay

While a camera is open live on the dashboard's «Jonli» page, the dashboard rewrites data\\live\\watch.json every
2 s (see camsvc.live_stream). While that request is fresh, this process keeps its own connection to the camera's
main stream and writes every new frame (~22 a second) as stream.jpg (1280 px) into the folder the dashboard named;
the browser loads it from the dashboard. So the live view runs at the camera's own speed, whatever target_fps the
analysis uses (2-3 frames a second per camera once there are many). IDLE_S after the last request the camera
connection is closed and the process only waits: no decoding, no GPU, nothing saved.
Stop: Ctrl+C or the file data\\STOP_LIVE. Logs: logs\\live_relay.log (the camera password is masked).
"""

from __future__ import annotations

import logging
import logging.handlers
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from camsvc.config import CHANNEL_MAIN, LOGS_DIR, SERVICE_DIR, camera_from_env, mask
from camsvc.live_stream import STREAM_MIN_GAP, watched_dir, write_stream
from camsvc.stream import LatestFrameReader

IDLE_S = 15.0  # the camera connection is kept this long after the last request, so coming back is instant
CHECK_S = 0.5  # how often watch.json is read
STOP_FILE = SERVICE_DIR / "data" / "STOP_LIVE"
log = logging.getLogger("live_relay")


class Relay:
    """One camera: connected only while the dashboard asks for its live view."""

    def __init__(self, open_reader: Callable[[], LatestFrameReader],
                 watch: Callable[[float], Path | None] = watched_dir):
        self.open_reader, self.watch = open_reader, watch
        self.reader: LatestFrameReader | None = None
        self.folder: Path | None = None
        self.last_seq = 0
        self.last_write = self.last_check = self.last_request = 0.0

    def step(self, now: float) -> None:
        """Re-read the request (every CHECK_S), connect or disconnect, and write the newest frame."""
        if now - self.last_check >= CHECK_S:
            self.last_check = now
            self.folder = self.watch(now)
            if self.folder is not None:
                self.last_request = now
        if self.folder is not None and self.reader is None:
            self.reader = self.open_reader()
            log.info("jonli ko'rish boshlandi")
        if self.reader is not None and self.folder is None and now - self.last_request > IDLE_S:
            self.reader.stop()
            self.reader, self.last_seq = None, 0
            log.info("jonli ko'rish to'xtadi: hech kim ko'rmayapti")
        if self.reader is None or self.folder is None:
            return
        frame = self.reader.wait_newer(self.last_seq, 0.2)
        if frame is None or frame.seq <= self.last_seq:
            return
        self.last_seq = frame.seq
        if now - self.last_write >= STREAM_MIN_GAP:
            self.last_write = now
            write_stream(self.folder, frame.image)

    def close(self) -> None:
        if self.reader is not None:
            self.reader.stop()
            self.reader = None


def setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOGS_DIR / "live_relay.log", maxBytes=2 * 2**20, backupCount=2,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(handler.formatter)
    log.setLevel(logging.INFO)
    log.handlers[:] = [handler, console]


def run() -> int:
    camera = camera_from_env()
    setup_logging()
    STOP_FILE.unlink(missing_ok=True)
    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: stop.set())
    log.info("start: kanal %s, faqat Jonli ochiq turganda ulanadi", CHANNEL_MAIN)
    relay = Relay(lambda: LatestFrameReader(lambda: camera.url(CHANNEL_MAIN), stall_seconds=10.0,
                                            log=lambda text: log.info(mask(text, camera)), name="jonli").start())
    last_stop_check = 0.0
    while not stop.is_set():
        now = time.time()
        if now - last_stop_check >= CHECK_S:
            last_stop_check = now
            if STOP_FILE.exists():
                log.info("STOP_LIVE fayli topildi")
                break
        relay.step(now)
        if relay.reader is None or relay.folder is None:  # not streaming: nothing to wait for but the next request
            stop.wait(CHECK_S)
    relay.close()
    log.info("to'xtadi")
    return 0


if __name__ == "__main__":
    sys.exit(run())
