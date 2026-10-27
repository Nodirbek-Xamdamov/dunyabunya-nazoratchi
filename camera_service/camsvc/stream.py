"""Latest-frame RTSP reader (OpenCV + FFmpeg, RTSP over TCP).

A background thread reads frames as fast as the camera sends them and keeps only
the newest one, so work done at target_fps never runs on an old, queued-up frame.
A failed read means the stream broke: the reader reconnects at once. If frames stop
coming (stall), the read times out and the reader reconnects; `stall_seconds` (30)
is the hard limit. Reconnect attempts wait 1, 2, 4 ... up to 30 s; the wait goes back
to 1 s after the first good frame.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

# Read by OpenCV's FFmpeg backend each time a capture is opened.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

BACKOFF_START, BACKOFF_MAX = 1.0, 30.0


@dataclass(frozen=True)
class Frame:
    image: np.ndarray
    seq: int  # frame number since the reader started
    wall: float  # time.time() when the frame was received (UTC epoch seconds)
    mono: float  # time.monotonic() when the frame was received


@dataclass
class ReaderStats:
    frames: int = 0
    connects: int = 0
    failed_connects: int = 0
    disconnects: int = 0
    stalls: int = 0
    connected: bool = False
    last_frame_wall: float | None = None
    last_error: str = ""
    gaps_over_1s: int = 0
    lost_frames_est: int = 0  # from arrival gaps longer than 2 frame intervals
    events: list[tuple[float, str]] = field(default_factory=list)


class LatestFrameReader:
    def __init__(self, url: Callable[[], str], *, stall_seconds: float = 30.0, open_timeout_s: float = 10.0,
                 read_timeout_s: float = 10.0, log: Callable[[str], None] = print, name: str = "camera"):
        self._url = url
        self.stall_seconds = stall_seconds
        self.open_timeout_s = open_timeout_s
        self.read_timeout_s = min(read_timeout_s, stall_seconds)
        self._log = log
        self._name = name
        self._lock = threading.Lock()
        self._new_frame = threading.Condition(self._lock)
        self._latest: Frame | None = None
        self._recent: deque[Frame] = deque(maxlen=3)  # the camera often sends 2 frames a few ms apart
        self._stop = threading.Event()
        self._drop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = ReaderStats()
        self.camera_fps = 0.0  # what the stream reports
        self.frame_size: tuple[int, int] | None = None

    # -- public -------------------------------------------------------------------------------------
    def start(self) -> LatestFrameReader:
        self._thread = threading.Thread(target=self._run, name=f"reader-{self._name}", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    def wait_newer(self, seq: int, timeout: float, max_lag: float = 0.0) -> Frame | None:
        """The newest frame; if it is not newer than `seq`, wait up to `timeout` s for one.
        The camera sends frames unevenly, so a tick can come just before the next frame.
        With `max_lag`, the oldest unseen frame of the last few is returned while it is at most that old,
        so a frame that arrived right behind another one is not overwritten unseen."""
        with self._new_frame:
            self._new_frame.wait_for(lambda: self._latest is not None and self._latest.seq > seq, timeout)
            if max_lag > 0:
                now = time.monotonic()
                for frame in self._recent:
                    if frame.seq > seq and now - frame.mono <= max_lag:
                        return frame
            return self._latest

    def _push(self, frame: Frame) -> None:
        with self._new_frame:
            self._latest = frame
            self._recent.append(frame)
            self._new_frame.notify_all()

    def drop_connection(self) -> None:
        """Close the current connection (used to test reconnecting)."""
        self._drop.set()

    # -- internals ----------------------------------------------------------------------------------
    def _event(self, text: str) -> None:
        self.stats.events.append((time.time(), text))
        self._log(f"[{self._name}] {text}")

    def _open(self) -> cv2.VideoCapture | None:
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(self.open_timeout_s * 1000),
                  cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self.read_timeout_s * 1000)]
        cap = cv2.VideoCapture(self._url(), cv2.CAP_FFMPEG, params)
        if not cap.isOpened():
            cap.release()
            return None
        self.camera_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        return cap

    def _run(self) -> None:
        backoff = BACKOFF_START
        seq = 0
        while not self._stop.is_set():
            started = time.monotonic()
            cap = self._open()
            if cap is None:
                self.stats.failed_connects += 1
                self.stats.last_error = "ulanib bo'lmadi"
                self._event(f"ulanib bo'lmadi ({time.monotonic() - started:.1f} s), {backoff:.0f} s dan keyin yana")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue
            self.stats.connects += 1
            self.stats.connected = True
            self._event(f"ulandi ({time.monotonic() - started:.1f} s, kamera {self.camera_fps:.0f} kadr/s)")
            last_good = time.monotonic()
            prev_arrival = None
            reason = "to'xtatildi"
            while not self._stop.is_set():
                if self._drop.is_set():
                    self._drop.clear()
                    reason = "ulanish ataylab uzildi (sinov)"
                    break
                ok, image = cap.read()
                now = time.monotonic()
                if not ok or image is None:
                    stalled = now - last_good
                    if stalled >= self.read_timeout_s - 0.5:
                        self.stats.stalls += 1
                        reason = f"{stalled:.0f} s kadr kelmadi"
                    else:
                        reason = "oqim uzildi (o'qish xatosi)"
                    break
                seq += 1
                if prev_arrival is not None and self.camera_fps > 0:
                    gap = now - prev_arrival
                    interval = 1.0 / self.camera_fps
                    if gap > 1.0:
                        self.stats.gaps_over_1s += 1
                    if gap > 2 * interval:
                        self.stats.lost_frames_est += int(round(gap / interval)) - 1
                prev_arrival = now
                frame = Frame(image=image, seq=seq, wall=time.time(), mono=now)
                self._push(frame)
                if self.frame_size is None:
                    self.frame_size = (image.shape[1], image.shape[0])
                self.stats.frames += 1
                self.stats.last_frame_wall = frame.wall
                last_good = now
                backoff = BACKOFF_START
            cap.release()
            self.stats.connected = False
            if self._stop.is_set():
                break
            self.stats.disconnects += 1
            self.stats.last_error = reason
            self._event(f"{reason}; {backoff:.0f} s dan keyin qayta ulanadi")
            self._stop.wait(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
