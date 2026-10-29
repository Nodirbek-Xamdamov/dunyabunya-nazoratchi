"""Save a few live frames with the server time they arrived, to compare with the camera's on-screen clock.

    .venv\\Scripts\\python.exe tools\\clock_check.py [--channel 101]

Writes review\\clock_<n>.png (only the clock corner of the frame) and prints the server time of each.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camsvc.config import PROJECT_DIR, camera_from_env  # noqa: E402
from camsvc.stream import LatestFrameReader  # noqa: E402

import cv2  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", type=int, default=101)
    parser.add_argument("--warmup", type=float, default=20.0, help="seconds of streaming before the first picture")
    args = parser.parse_args()
    camera = camera_from_env()
    reader = LatestFrameReader(lambda: camera.url(args.channel), log=lambda _m: None).start()
    out = PROJECT_DIR / "review"
    out.mkdir(exist_ok=True)
    time.sleep(args.warmup)
    last = reader.latest().seq if reader.latest() else 0
    taken = 0
    deadline = time.monotonic() + 30
    while taken < 12 and time.monotonic() < deadline:
        frame = reader.wait_newer(last, 1.0)
        if frame is None or frame.seq == last:
            continue
        last = frame.seq
        if taken == 0 or frame.wall - prev >= 0.25:
            h, w = frame.image.shape[:2]
            corner = frame.image[: int(h * 0.09), : int(w * 0.32)]
            cv2.imwrite(str(out / f"clock_{taken:02d}.png"), corner)
            local = dt.datetime.fromtimestamp(frame.wall).strftime("%H:%M:%S.%f")[:-3]
            print(f"clock_{taken:02d}.png  server {local}")
            prev = frame.wall
            taken += 1
    reader.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
