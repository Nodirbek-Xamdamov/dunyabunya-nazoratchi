"""W4-01: sample frames for lighting checks (day / evening / backlight).

One frame now:          python samples.py --label kunduz
Every 30 min until 20:00: python samples.py --every 30 --until 20:00
Frames stay on this server: intake/out/samples/<date>/<HH-MM>_<label>_ch<N>.jpg
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from camlib import SAMPLES, SecretError, load_camera, now_local, snapshot


def take(cam, channels: list[int], label: str) -> bool:
    t = now_local()
    ok_all = True
    for ch in channels:
        dest = SAMPLES / f"{t:%Y-%m-%d}" / f"{t:%H-%M}_{label}_ch{ch}.jpg"
        ok, err = snapshot(cam, ch, dest)
        ok_all &= ok
        print(f"{t:%H:%M:%S} ch{ch} {'OK ' + dest.name if ok else 'FAIL ' + err}", flush=True)
    return ok_all


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", type=int, nargs="+", default=[102, 101])
    ap.add_argument("--label", default="auto")
    ap.add_argument("--every", type=float, default=0, help="minutes between frames; 0 = one frame")
    ap.add_argument("--until", default="20:00", help="local HH:MM to stop the loop")
    args = ap.parse_args()
    try:
        cam = load_camera()
    except SecretError as e:
        print(f"ERROR: {e}")
        return 2
    if not args.every:
        return 0 if take(cam, args.channels, args.label) else 1

    hh, mm = map(int, args.until.split(":"))
    stop = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    fails = 0
    while datetime.now() < stop:
        fails = 0 if take(cam, args.channels, args.label) else fails + 1
        if fails >= 3:
            print("3 failures in a row, stopping")
            return 1
        time.sleep(args.every * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
