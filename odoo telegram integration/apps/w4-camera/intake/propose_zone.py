"""W4-01: propose the door zone from one frame with LOCAL tools only (the frame never leaves the server).

1. Draws a labelled grid (A..H x 1..6) on the frame -> intake/out/zone_grid_<frame>.png
2. Asks the local Gemma 4 on Ollama, on the CPU (num_gpu 0, no GPU use, unloaded right after),
   where the door is, whether something blocks it, where its threshold is. Two prompts are asked
   (grid cells and a direct box) so their agreement shows how sure the answer is.
3. Snaps the door sides to the strongest vertical edges near the answer (OpenCV).
4. Writes the proposal (zone_tool.html format) -> intake/out/zone_proposal_<frame>.json
Usage: <python with opencv> propose_zone.py --frame out/samples/<date>/<file>.jpg
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from camlib import OUT, now_local, write_json

OLLAMA = "http://127.0.0.1:11434/api/generate"
COLS, ROWS = "ABCDEFGH", 6

ASK_CELLS = """This picture is from a fixed security camera INSIDE an office room. The camera looks towards the room's door.
A grid is drawn on it: columns A-H (left to right), rows 1-6 (top to bottom), cell names like C4.
Answer only with JSON:
{"scene": "<one sentence>", "door_visible": true/false, "door_cells": ["<every cell that contains part of the door or doorway>"],
 "door_kind": "<glass/wood/metal/open doorway/unknown>", "door_open": "open/closed/unknown",
 "blocked": true/false, "blocking": "<what stands in front of the door, or empty>",
 "floor_at_door_visible": true/false, "other_doors_or_openings": ["<cells>"], "confidence": "high/medium/low"}"""

ASK_BOX = """This picture is from a fixed security camera INSIDE an office room, looking towards the room's door.
Give the door (or doorway) as a box in relative coordinates, 0 = left/top edge of the picture, 1 = right/bottom edge.
Answer only with JSON: {"door_box": [x_left, y_top, x_right, y_bottom], "threshold_y": <y of the floor line at the bottom of the door>,
"room_side": "<where the room interior is relative to the door: below/left/right>", "confidence": "high/medium/low"}"""


def load(path: str):
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"cannot read {path}")
    return img


def grid_image(img):
    g = cv2.resize(img, (img.shape[1] * 2, img.shape[0] * 2), interpolation=cv2.INTER_CUBIC)
    h, w = g.shape[:2]
    for i in range(1, len(COLS)):
        cv2.line(g, (i * w // len(COLS), 0), (i * w // len(COLS), h), (0, 255, 255), 1)
    for j in range(1, ROWS):
        cv2.line(g, (0, j * h // ROWS), (w, j * h // ROWS), (0, 255, 255), 1)
    for i, c in enumerate(COLS):
        for j in range(ROWS):
            cv2.putText(g, f"{c}{j + 1}", (i * w // len(COLS) + 4, j * h // ROWS + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
    return g


def ask(model: str, prompt: str, img) -> dict:
    ok, buf = cv2.imencode(".png", img)
    payload = {"model": model, "prompt": prompt, "images": [base64.b64encode(buf.tobytes()).decode()],
               "stream": False, "think": False, "format": "json", "keep_alive": 0,
               "options": {"num_gpu": 0, "temperature": 0, "num_ctx": 8192, "num_predict": 400}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        resp = json.loads(r.read())
    try:
        return json.loads(resp["response"])
    except json.JSONDecodeError:
        return {"raw": resp["response"][:500]}


def cells_box(cells: list[str]) -> list[float] | None:
    pts = []
    for c in cells or []:
        c = str(c).strip().upper()
        if len(c) >= 2 and c[0] in COLS and c[1:].isdigit() and 1 <= int(c[1:]) <= ROWS:
            pts.append((COLS.index(c[0]), int(c[1:]) - 1))
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs) / len(COLS), min(ys) / ROWS, (max(xs) + 1) / len(COLS), (max(ys) + 1) / ROWS]


def box_iou(p, q) -> float:
    ix = max(0.0, min(p[2], q[2]) - max(p[0], q[0]))
    iy = max(0.0, min(p[3], q[3]) - max(p[1], q[1]))
    ua = (p[2] - p[0]) * (p[3] - p[1]) + (q[2] - q[0]) * (q[3] - q[1]) - ix * iy
    return ix * iy / ua if ua > 0 else 0.0


def normalise_box(raw, ref: list[float] | None) -> tuple[list[float] | None, str]:
    """Gemma often answers on a 0..1000 scale and sometimes as [y1, x1, y2, x2].
    Scale to 0..1 and pick the axis order that agrees best with the grid-cell answer."""
    if not (isinstance(raw, list) and len(raw) == 4 and all(isinstance(v, (int, float)) for v in raw)):
        return None, "none"
    scale = 1000.0 if max(raw) > 1.5 else 1.0
    v = [float(x) / scale for x in raw]
    xy, yx = [v[0], v[1], v[2], v[3]], [v[1], v[0], v[3], v[2]]
    if ref and box_iou(yx, ref) > box_iou(xy, ref):
        return yx, f"scale 1/{scale:g}, order y1,x1,y2,x2"
    return xy, f"scale 1/{scale:g}, order x1,y1,x2,y2"


def snap_sides(img, box: list[float]) -> tuple[list[float], dict]:
    """Move the left/right sides to the strongest long vertical edge within +-1/16 of the width."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    y1, y2 = int(box[1] * h), max(int(box[1] * h) + 2, int(box[3] * h))
    profile = gx[y1:y2].mean(axis=0)
    out, info = list(box), {}
    for k, idx in (("left", 0), ("right", 2)):
        x = int(box[idx] * w)
        lo, hi = max(0, x - w // 16), min(w, x + w // 16)
        if hi - lo > 2:
            best = lo + int(np.argmax(profile[lo:hi]))
            strength = float(profile[best] / (profile.mean() + 1e-6))
            info[k] = {"from": round(box[idx], 3), "to": round(best / w, 3), "edge_vs_mean": round(strength, 2)}
            if strength >= 1.5:  # only snap to a clearly visible edge
                out[idx] = best / w
    return out, info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    ap.add_argument("--model", default="gemma4:12b")
    ap.add_argument("--reuse", action="store_true", help="reuse the saved model answers, redo only the geometry")
    args = ap.parse_args()
    img = load(args.frame)
    stem = Path(args.frame).stem
    grid = grid_image(img)
    ok, buf = cv2.imencode(".png", grid)
    buf.tofile(str(OUT / f"zone_grid_{stem}.png"))

    saved = OUT / f"zone_proposal_{stem}.json"
    if args.reuse and saved.exists():  # post-processing only, the model is not asked again
        old = json.loads(saved.read_text(encoding="utf-8"))
        a, b = old["cells_answer"], old["box_answer"]
    else:
        a = ask(args.model, ASK_CELLS, grid)
        b = ask(args.model, ASK_BOX, img)
    print("cells answer:", json.dumps(a, ensure_ascii=False), flush=True)
    print("box answer:", json.dumps(b, ensure_ascii=False), flush=True)

    box_cells = cells_box(a.get("door_cells", []))
    box_direct, box_note = normalise_box(b.get("door_box"), box_cells)
    if not (box_cells or box_direct):
        print("no door found by the model")
        write_json(saved, {"cells_answer": a, "box_answer": b, "proposal": None})
        return 1
    agree = round(box_iou(box_cells, box_direct), 2) if box_cells and box_direct else None
    base = box_direct or box_cells
    door, snap = snap_sides(img, [float(v) for v in base])
    thr = b.get("threshold_y")
    if isinstance(thr, (int, float)) and thr > 1.5:
        thr = thr / 1000.0
    thr = float(thr) if isinstance(thr, (int, float)) and door[1] < float(thr) <= 1 else door[3]
    x1, x2 = door[0], door[2]
    depth = min(0.12, 1 - thr)  # zone reaches a little into the room in front of the door
    proposal = {
        "image": [img.shape[1], img.shape[0]],
        "door_zone": [[round(x1, 4), round(door[1], 4)], [round(x2, 4), round(door[1], 4)],
                      [round(min(1, x2 + 0.03), 4), round(min(1, thr + depth), 4)],
                      [round(max(0, x1 - 0.03), 4), round(min(1, thr + depth), 4)]],
        "cross_line": [[round(max(0, x1 - 0.02), 4), round(min(1, thr + depth / 2), 4)],
                       [round(min(1, x2 + 0.02), 4), round(min(1, thr + depth / 2), 4)]],
        "inside_point": [round((x1 + x2) / 2, 4), round(min(0.98, thr + depth + 0.08), 4)],
    }
    result = {"frame": args.frame, "at": now_local().isoformat(timespec="seconds"), "model": args.model,
              "device": "cpu (num_gpu 0)", "cells_answer": a, "box_answer": b, "box_from_cells": box_cells,
              "box_direct": box_direct, "box_direct_note": box_note, "boxes_iou": agree, "snap": snap, "door_box_final": [round(v, 4) for v in door],
              "threshold_y": round(thr, 4), "proposal": proposal}
    write_json(OUT / f"zone_proposal_{stem}.json", result)
    print(json.dumps({k: result[k] for k in ("box_from_cells", "box_direct", "boxes_iou", "snap", "door_box_final",
                                             "threshold_y")}, ensure_ascii=False))
    print(json.dumps(proposal))
    return 0


if __name__ == "__main__":
    sys.exit(main())
