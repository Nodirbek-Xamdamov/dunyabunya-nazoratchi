"""W4-01: draw the door zone, the crossing line and the "inside" side on a frame (for the user to check).

The coordinates (0..1) come from zone_config.json ("polygon") or the older zone JSON ("door_zone").
The picture stays on this server.
Usage: python draw_zone.py --frame out/live/<file>.jpg --zone zone_config.json [--out out/zone_overlay.png]
Needs opencv + numpy (e.g. the legacy service venv, only executed).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from camlib import OUT


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    ap.add_argument("--zone", required=True, help="JSON file or JSON text from zone_tool.html")
    ap.add_argument("--out")
    args = ap.parse_args()
    z = Path(args.zone)
    zone = json.loads(z.read_text(encoding="utf-8") if z.exists() else args.zone)

    img = cv2.imdecode(np.fromfile(args.frame, np.uint8), cv2.IMREAD_COLOR)  # Cyrillic-safe read
    if img is None:
        print(f"cannot read frame {args.frame}")
        return 1
    h, w = img.shape[:2]
    px = lambda p: (int(round(p[0] * w)), int(round(p[1] * h)))  # noqa: E731
    th = max(2, w // 320)

    poly = np.array([px(p) for p in zone.get("door_zone") or zone.get("polygon") or []], np.int32)  # zone_tool / zone_config.json
    if len(poly) >= 3:
        layer = img.copy()
        cv2.fillPoly(layer, [poly], (42, 89, 224))
        img = cv2.addWeighted(layer, 0.30, img, 0.70, 0)
        cv2.polylines(img, [poly], True, (42, 89, 224), th)
    line = zone.get("cross_line") or []
    if len(line) == 2:
        a, b = px(line[0]), px(line[1])
        cv2.line(img, a, b, (224, 122, 31), th * 2)
        for q in (a, b):
            cv2.circle(img, q, th * 3, (224, 122, 31), -1)
    inside = zone.get("inside_point")
    if inside:
        c = px(inside)
        cv2.circle(img, c, th * 5, (85, 154, 26), -1)
        cv2.putText(img, "ichkari", (c[0] + th * 6, c[1] + th * 2), cv2.FONT_HERSHEY_SIMPLEX,
                    0.4 * th, (85, 154, 26), max(1, th // 2), cv2.LINE_AA)
        if len(line) == 2:
            mid = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
            cv2.arrowedLine(img, mid, c, (85, 154, 26), th, tipLength=0.15)

    out = Path(args.out) if args.out else OUT / f"zone_overlay_{Path(args.frame).stem}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        print("encode failed")
        return 1
    buf.tofile(str(out))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
