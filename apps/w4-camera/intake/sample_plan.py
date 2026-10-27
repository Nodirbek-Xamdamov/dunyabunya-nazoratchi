"""W4-01: lighting samples on a schedule (single frames only, no recording).

Every --every minutes between --start and --end, skipping the night window, one frame from
channel 102; at each --main-at time one frame from 101 as well. Runs detached; stop it with
the file intake/out/STOP_SAMPLES. Log: intake/out/sample_plan.log
Usage: python sample_plan.py --start "2026-10-26 13:30" --end "2026-10-27 20:00" --every 30 --main-at "2026-10-26 18:30"
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta

from camlib import OUT, SAMPLES, SecretError, load_camera, snapshot

STOP = OUT / "STOP_SAMPLES"
LOG = OUT / "sample_plan.log"


def label(t: datetime) -> str:
    hm = t.hour * 60 + t.minute
    if hm < 10 * 60:
        return "ertalab"
    if hm < 17 * 60 + 30:
        return "kunduz"
    return "kech"


def log(msg: str) -> None:
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def slots(start: datetime, end: datetime, every: int, day_from: str, day_to: str) -> list[datetime]:
    h1, m1 = map(int, day_from.split(":"))
    h2, m2 = map(int, day_to.split(":"))
    out, t = [], start
    while t <= end:
        hm = t.hour * 60 + t.minute
        if h1 * 60 + m1 <= hm <= h2 * 60 + m2:
            out.append(t)
        t += timedelta(minutes=every)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--every", type=int, default=30)
    ap.add_argument("--day-from", default="07:30")
    ap.add_argument("--day-to", default="20:00")
    ap.add_argument("--main-at", nargs="*", default=[])
    args = ap.parse_args()
    try:
        cam = load_camera()
    except SecretError as e:
        log(f"ERROR: {e}")
        return 2
    fmt = "%Y-%m-%d %H:%M"
    main_at = {datetime.strptime(x, fmt) for x in args.main_at}
    plan = slots(datetime.strptime(args.start, fmt), datetime.strptime(args.end, fmt), args.every,
                 args.day_from, args.day_to)
    plan = sorted(set(plan) | main_at)
    log(f"start: {len(plan)} slots, {plan[0]:%m-%d %H:%M} .. {plan[-1]:%m-%d %H:%M}, 101 at {sorted(x.strftime(fmt) for x in main_at)}")
    fails = 0
    for t in plan:
        while datetime.now() < t:
            if STOP.exists():
                log("stopped by STOP_SAMPLES")
                return 0
            time.sleep(min(20.0, max(0.5, (t - datetime.now()).total_seconds())))
        if (datetime.now() - t).total_seconds() > 600:
            log(f"skip {t:%m-%d %H:%M} (late)")
            continue
        for ch in ([102, 101] if t in main_at else [102]):
            dest = SAMPLES / f"{t:%Y-%m-%d}" / f"{t:%H-%M}_{label(t)}_ch{ch}.jpg"
            ok, err = snapshot(cam, ch, dest)
            fails = 0 if ok else fails + 1
            log(f"ch{ch} {'OK ' + dest.name if ok else 'FAIL ' + err}")
        if fails >= 3:
            log("3 failures in a row, stopping (no more retries against the camera)")
            return 1
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
