"""Create camera.db (cam_* tables, default settings) and fill the desks and employees once.

    .venv\\Scripts\\python.exe tools\\init_db.py
    .venv\\Scripts\\python.exe tools\\init_db.py --frame-from ..\\test_clips\\kamera101_....mkv --at 120

--frame-from puts one frame of a recorded clip into data\\live as the picture for drawing desk zones
(until the service runs and replaces it with the live view); latest.json says it came from the clip.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camsvc import db  # noqa: E402

# User's answer of 2026-10-20: far desk Nodir, middle Husniddin, nearest to the camera Muhammadumar (men: 09:00-19:00)
DESKS = [("Uzoq stol", "Nodir"), ("O'rta stol", "Husniddin"), ("Yaqin stol", "Muhammadumar")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frame-from", type=Path)
    parser.add_argument("--at", type=float, default=120.0, help="seconds from the clip start")
    args = parser.parse_args()
    conn = db.connect()
    db.init(conn)
    added = db.seed(conn, DESKS)
    print(f"baza: {db.DB_PATH} ({'yangi stollar: ' + str(added) if added else 'stollar avvaldan bor'})")
    if args.frame_from:
        import cv2

        from camsvc.pipeline import write_live

        cap = cv2.VideoCapture(str(args.frame_from))
        cap.set(cv2.CAP_PROP_POS_MSEC, args.at * 1000)
        ok, frame = cap.read()
        if not ok:
            print("kadr o'qilmadi")
            return 1
        meta = json.loads(args.frame_from.with_suffix(".json").read_text(encoding="utf-8"))
        start = dt.datetime.fromisoformat(meta.get("start_utc") or meta["started_utc"]).timestamp()
        write_live(frame, frame, start + args.at, source=f"clip:{args.frame_from.name}")
        print(f"kadr: {args.frame_from.name} +{args.at:.0f} s -> data\\live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
