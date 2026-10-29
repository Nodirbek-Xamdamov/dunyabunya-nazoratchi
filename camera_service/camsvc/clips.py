"""5-second videos of every arrival at a desk and every departure from it, to check the table by eye.

The pipeline hands every analysed frame to `ClipRecorder.add`; the last BUFFER_S seconds are kept as small
JPEGs (at most CLIP_WIDTH wide, with the time printed on them). An arrival video covers [start - 3 s, start + 2 s]
and is saved as soon as the interval opens (that is decided 6-10 s after the start, so the frames are still in
the buffer). A departure is known only `away_grace_s` later, long after the buffer has moved on, so while the
interval is open and the person has not been seen for AFTER_OUT seconds, the frames around the last sighting are
put aside. If the interval then closes at that last sighting ("away"), they become the departure video; if the
person comes back, the next absence replaces them.

Files: data\\clips\\<source>\\YYYY-MM-DD\\<employee id>_<HH-MM-SS>_<in|out>.mp4 (H.264, plays in the browser),
written by ffmpeg in a background thread. If ffmpeg is missing or fails, the middle frame is saved as .jpg next
to it instead. Rows in cam_clips; live videos older than `clip_keep_days` are deleted.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from camsvc import db

CLIPS_DIR = Path(os.environ.get("CAMERA_CLIPS_DIR") or db.DB_PATH.parent / "clips")
FFMPEG = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg.exe"
BEFORE_IN, AFTER_IN = 3.0, 2.0  # arrival: the person walks up and sits down
BEFORE_OUT, AFTER_OUT = 2.0, 3.0  # departure: last sighting, then walking away
BUFFER_S = 20.0
CLIP_WIDTH = 960
log = logging.getLogger("camsvc.clips")


def source_dir(source: str) -> str:
    """Folder name for a source: "live", or the clip file name without unsafe characters."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", source)


class ClipRecorder:
    def __init__(self, conn, source: str = "live", tz: dt.tzinfo | None = None, clips_dir: Path = CLIPS_DIR,
                 ffmpeg: str | None = FFMPEG):
        self.conn, self.source, self.tz, self.clips_dir = conn, source, tz, clips_dir
        self.ffmpeg = ffmpeg if ffmpeg and Path(ffmpeg).exists() else None
        if self.ffmpeg is None:
            log.warning("ffmpeg topilmadi: chiqish/kirish videolari o'rniga bitta kadr (.jpg) saqlanadi")
        self._frames: deque[tuple[float, bytes]] = deque()
        self._pending: dict[int, tuple[float, list[bytes]]] = {}  # desk id -> (last sighting, frames around it)
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._writer, name="clip-writer", daemon=True)
        self._thread.start()

    # -- called by the pipeline ---------------------------------------------------------------------
    def add(self, t: float, image: np.ndarray) -> None:
        height, width = image.shape[:2]
        if width > CLIP_WIDTH:
            new_h = int(round(height * CLIP_WIDTH / width / 2)) * 2
            image = cv2.resize(image, (CLIP_WIDTH, new_h), interpolation=cv2.INTER_AREA)
        else:
            image = image[: height // 2 * 2, : width // 2 * 2].copy()  # H.264 needs even sizes
        stamp = dt.datetime.fromtimestamp(t, self.tz).strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(image, stamp, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
        cv2.putText(image, stamp, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            return
        self._frames.append((t, data.tobytes()))
        while self._frames and self._frames[0][0] < t - BUFFER_S:
            self._frames.popleft()

    def watch(self, desk_id: int, last_seen: float | None, t: float) -> None:
        """Every frame in which the desk's interval is open but its person is not seen."""
        if last_seen is None or t < last_seen + AFTER_OUT:
            return
        if desk_id in self._pending and self._pending[desk_id][0] == last_seen:
            return
        self._pending[desk_id] = (last_seen, self._between(last_seen - BEFORE_OUT, last_seen + AFTER_OUT))

    def arrival(self, desk_id: int, employee_id: int | None, interval_id: int, start: float) -> None:
        self._pending.pop(desk_id, None)
        self._save(desk_id, employee_id, interval_id, "in", start, self._between(start - BEFORE_IN, start + AFTER_IN))

    def departure(self, desk_id: int, employee_id: int | None, interval_id: int, end: float) -> None:
        last_seen, frames = self._pending.pop(desk_id, (None, []))
        if last_seen != end:
            frames = self._between(end - BEFORE_OUT, end + AFTER_OUT)
        self._save(desk_id, employee_id, interval_id, "out", end, frames)

    def close(self, timeout: float = 30.0) -> None:
        """Wait until the queued videos are written (end of a clip replay, service stop)."""
        self._queue.put(None)
        self._thread.join(timeout)

    def cleanup(self, keep_days: float, now: float | None = None) -> int:
        """Delete live videos (files and rows) older than keep_days; returns how many were deleted."""
        cutoff = (now or time.time()) - keep_days * 86400
        rows = self.conn.execute("SELECT id, path FROM cam_clips WHERE source = ? AND event_ts < ?",
                                 (self.source, cutoff)).fetchall()
        for row in rows:
            path = self.clips_dir / row["path"]
            for candidate in (path, path.with_suffix(".jpg")):
                candidate.unlink(missing_ok=True)
            try:
                path.parent.rmdir()  # only when the day folder is empty
            except OSError:
                pass
        if rows:
            with self.conn:
                self.conn.executemany("DELETE FROM cam_clips WHERE id = ?", [(row["id"],) for row in rows])
        return len(rows)

    # -- internals ----------------------------------------------------------------------------------
    def _between(self, start: float, end: float) -> list[bytes]:
        return [data for ts, data in self._frames if start <= ts <= end]

    def _save(self, desk_id: int, employee_id: int | None, interval_id: int, kind: str, ts: float,
              frames: list[bytes]) -> None:
        if not frames:
            return
        local = dt.datetime.fromtimestamp(ts, self.tz)
        who = str(employee_id) if employee_id is not None else f"stol{desk_id}"
        rel = f"{source_dir(self.source)}/{local:%Y-%m-%d}/{who}_{local:%H-%M-%S}_{kind}.mp4"
        db.add_clip(self.conn, self.source, desk_id, employee_id, interval_id, kind, ts, rel)
        span = BEFORE_IN + AFTER_IN if kind == "in" else BEFORE_OUT + AFTER_OUT
        self._queue.put((self.clips_dir / rel, frames, max(1.0, len(frames) / span)))

    def _writer(self) -> None:
        while (job := self._queue.get()) is not None:
            path, frames, fps = job
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if not self._encode(path, frames, fps):
                    path.with_suffix(".jpg").write_bytes(frames[len(frames) // 2])
            except OSError as exc:
                log.warning("video yozilmadi %s: %s", path.name, exc)

    def _encode(self, path: Path, frames: list[bytes], fps: float) -> bool:
        if self.ffmpeg is None:
            return False
        tmp = path.with_name(path.stem + ".part.mp4")
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe", "-c:v", "mjpeg",
               "-framerate", f"{fps:.3f}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)]
        try:
            done = subprocess.run(cmd, input=b"".join(frames), capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("ffmpeg ishlamadi: %s", exc)
            return False
        if done.returncode != 0:
            log.warning("ffmpeg xatosi: %s", done.stderr.decode(errors="replace")[-300:])
            tmp.unlink(missing_ok=True)
            return False
        os.replace(tmp, path)
        return True
