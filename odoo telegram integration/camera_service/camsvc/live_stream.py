"""Smooth live view for the dashboard, only while someone watches it (the request and the frame file).

While a camera is open live on the dashboard's «Jonli» page, the dashboard rewrites data\\live\\watch.json every
2 s: {"ts": <unix time>, "dir": "<dashboard>\\static\\kamera"}. While that file is fresh, camsvc.live_relay writes
the camera's frames as a small JPEG, stream.jpg, into that folder; the dashboard serves it as a static file and
the browser loads it again and again. Nobody watching = nothing is written.
The camera sends frames unevenly (often 20-40 ms apart), so the gap between stream frames is only kept above
STREAM_MIN_GAP: nearly every camera frame (~22 a second) reaches the stream."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import cv2
import numpy as np

from camsvc.pipeline import LIVE_DIR

STREAM_MIN_GAP = 0.025  # s, so at most 40 stream frames a second even when frames come in a bunch
STREAM_WIDTH = 1280
WATCH_S = 6.0  # the dashboard rewrites watch.json every 2 s
WATCH_FILE = LIVE_DIR / "watch.json"


def watched_dir(now: float | None = None, watch_file: Path | None = None) -> Path | None:
    """The folder the dashboard wants the stream in, while its request is fresh; otherwise None."""
    try:
        request = json.loads((watch_file or WATCH_FILE).read_text(encoding="utf-8"))
        folder = Path(request["dir"])
        fresh = (now or time.time()) - float(request["ts"]) <= WATCH_S
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not fresh or folder.name != "kamera" or folder.parent.name != "static" or not folder.is_dir():
        return None  # only a dashboard's static\kamera folder
    return folder


def write_stream(folder: Path, image: np.ndarray) -> bool:
    """One try only: while the browser is reading the previous frame the replace fails, and this frame is skipped
    (the next one comes ~50 ms later) instead of holding up the analysis loop."""
    height, width = image.shape[:2]
    if width > STREAM_WIDTH:
        image = cv2.resize(image, (STREAM_WIDTH, round(height * STREAM_WIDTH / width)), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if not ok:
        return False
    tmp = folder / "stream.tmp"
    try:
        tmp.write_bytes(data.tobytes())
        os.replace(tmp, folder / "stream.jpg")
    except OSError:
        return False
    return True
