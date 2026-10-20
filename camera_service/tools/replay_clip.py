"""Stage 4: run a recorded clip through the full pipeline on video time, faster than real time.

    .venv\\Scripts\\python.exe tools\\replay_clip.py ..\\test_clips\\kamera101_....mkv [--fps 5]

Frames are taken every 1/fps seconds of video; each frame gets the real time it was recorded
(clip start from the .json next to the clip + position in the video). Results go to camera.db with
source "clip:<file name>"; an earlier run of the same clip is replaced. Desks and settings are the
ones saved in the dashboard (target_fps can be overridden with --fps).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camsvc import runtime  # noqa: E402  (must come before ultralytics)
from camsvc import db  # noqa: E402
from camsvc.pipeline import Pipeline  # noqa: E402

import cv2  # noqa: E402


def clip_start(clip: Path) -> float:
    meta = json.loads(clip.with_suffix(".json").read_text(encoding="utf-8"))
    return dt.datetime.fromisoformat(meta.get("first_bytes_utc") or meta["started_utc"]).timestamp()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clip", type=Path)
    parser.add_argument("--fps", type=float)
    args = parser.parse_args()
    runtime.check()

    conn = db.connect()
    db.init(conn)
    settings = db.settings(conn)
    if args.fps:
        settings["target_fps"] = args.fps
    desks = db.active_desks(conn)
    if not any(len(d["zone"]) >= 3 for d in desks):
        print("Stol zonalari hali chizilmagan (Yangi kamera -> Sozlash).")
        return 1
    source = f"clip:{args.clip.name}"
    with conn:
        conn.execute("DELETE FROM cam_intervals WHERE source = ?", (source,))
        conn.execute("DELETE FROM cam_coverage WHERE source = ?", (source,))
    start_ts = clip_start(args.clip)
    pipeline = Pipeline(conn, settings, desks, source=source)
    cap = cv2.VideoCapture(str(args.clip), cv2.CAP_FFMPEG)
    period_ms = 1000.0 / float(settings["target_fps"])
    next_ms = None
    processed = 0
    per_minute: dict[int, int] = {}
    started = time.monotonic()
    pos = 0.0
    while cap.grab():
        pos = cap.get(cv2.CAP_PROP_POS_MSEC)
        if next_ms is None:
            next_ms = pos
        if pos + 1e-3 < next_ms:
            continue
        next_ms += period_ms
        ok, frame = cap.retrieve()
        if not ok:
            continue
        t = start_ts + pos / 1000
        pipeline.process(frame, t)
        processed += 1
        minute = int(t // 60) * 60
        per_minute[minute] = per_minute.get(minute, 0) + 1
    pipeline.stop(close=True)
    cap.release()
    fps = float(settings["target_fps"])
    for minute, count in per_minute.items():
        seconds = min(60.0, start_ts + pos / 1000 - minute, minute + 60 - start_ts)
        db.add_coverage(conn, source, minute, round(max(0.0, seconds) * fps), count, 0, max(0.0, seconds))
    elapsed = time.monotonic() - started
    rows = conn.execute("SELECT COUNT(*), SUM(end_ts - start_ts) FROM cam_intervals WHERE source = ?", (source,)).fetchone()
    print(f"{args.clip.name}: video {pos / 60000:.1f} daq, {processed} kadr, {elapsed:.0f} s "
          f"({pos / 1000 / max(elapsed, 1e-6):.1f}x real vaqtdan tez); intervallar {rows[0]}, jami {(rows[1] or 0) / 60:.1f} daq")
    return 0


if __name__ == "__main__":
    sys.exit(main())
