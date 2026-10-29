"""Stage 2: picture sheet of the frames where a configuration and the reference disagree.

    .venv\\Scripts\\python.exe tools\\compare_sheet.py m_640_roi [--max 8]

Green = reference (yolo26x, 1280), red = the configuration (conf >= 0.35). Desk-area crops only.
Output: review\\compare\\sheet_<name>.jpg
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compare_models import DESK_REGION, REFERENCE, WORK, in_desk_region, iou  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("--max", type=int, default=8)
    parser.add_argument("--conf", type=float, default=0.35)
    args = parser.parse_args()
    ref = {f["name"]: f["boxes"] for f in json.loads((WORK / f"run_{REFERENCE['name']}.json").read_text("utf-8"))["frames"]}
    run = json.loads((WORK / f"run_{args.name}.json").read_text("utf-8"))
    index = {f["name"]: f for f in json.loads((WORK / "index.json").read_text("utf-8"))["frames"]}
    picked = []
    for frame in run["frames"]:
        cand = [b for b in frame["boxes"] if b[4] >= args.conf and in_desk_region(b)]
        truth = [b for b in ref[frame["name"]] if b[4] >= 0.5 and in_desk_region(b)]
        extra = [b for b in cand if max((iou(b, t) for t in truth), default=0) < 0.4]
        missed = [t for t in truth if max((iou(b, t) for b in cand), default=0) < 0.4]
        if extra or missed:
            picked.append((frame["name"], cand, truth, len(extra), len(missed)))
    print(f"{args.name}: farq bor kadrlar {len(picked)} / {len(run['frames'])}")
    step = max(1, len(picked) // args.max)
    tiles = []
    x1, y1, x2, y2 = DESK_REGION[0] - 180, DESK_REGION[1] - 180, DESK_REGION[2], DESK_REGION[3]
    for name, cand, truth, n_extra, n_missed in picked[::step][: args.max]:
        image = cv2.imread(str(WORK / f"{name}_101.jpg"))
        for b in truth:
            cv2.rectangle(image, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (60, 220, 60), 5)
        for b in cand:
            cv2.rectangle(image, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (40, 40, 255), 2)
            cv2.putText(image, f"{b[4]:.2f}", (int(b[0]) + 6, int(b[3]) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 40, 255), 2)
        tile = cv2.resize(image[y1:y2, x1:x2], None, fx=0.6, fy=0.6)
        label = f"{index[name]['clock']}  +{n_extra} ortiqcha  -{n_missed} topilmagan"
        cv2.rectangle(tile, (0, 0), (tile.shape[1], 34), (0, 0, 0), -1)
        cv2.putText(tile, label, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        tiles.append(tile)
    if not tiles:
        return 0
    while len(tiles) % 2:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)]
    out = WORK / f"sheet_{args.name}.jpg"
    cv2.imwrite(str(out), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
