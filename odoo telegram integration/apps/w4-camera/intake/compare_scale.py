r"""W4-01: person size on 101 (1920x1080) vs 102 (640x360), from single frames (no recording).

Grabs 101 and 102 at the same moment, finds people on the CPU (GPU is not touched, W0 gate),
matches them by position and reports box heights. Needs a Python with ultralytics, e.g. the
legacy service venv (only executed, nothing installed or written there):
  <camera_service>\.venv\Scripts\python.exe compare_scale.py --pairs 4 --every 15
Result: intake/out/compare_scale_<time>.json, frames in intake/out/samples/<date>/scale/
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

from camlib import OUT, SAMPLES, SecretError, load_camera, now_local, snapshot, write_json

os.environ["CUDA_VISIBLE_DEVICES"] = ""  # CPU only
os.environ.setdefault("YOLO_CONFIG_DIR", str(OUT / "yolo_cfg"))  # keep ultralytics settings out of %APPDATA%
os.environ.setdefault("YOLO_OFFLINE", "1")

DEFAULT_MODEL = Path(__file__).resolve().parents[3] / "camera_service" / "models" / "yolo26m.pt"


def grab_pair(cam, folder: Path, stamp: str) -> dict[int, Path]:
    paths = {ch: folder / f"{stamp}_ch{ch}.jpg" for ch in (101, 102)}
    res: dict[int, bool] = {}

    def one(ch):
        res[ch] = snapshot(cam, ch, paths[ch])[0]

    threads = [threading.Thread(target=one, args=(ch,)) for ch in paths]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    return {ch: p for ch, p in paths.items() if res.get(ch)}


def people(model, path: Path, conf: float) -> tuple[list[dict], tuple[int, int]]:
    r = model.predict(str(path), device="cpu", classes=[0], conf=conf, imgsz=640, verbose=False)[0]
    h, w = r.orig_shape
    out = []
    for (x1, y1, x2, y2), c in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist()):
        out.append({"conf": round(c, 3), "h_px": round(y2 - y1), "w_px": round(x2 - x1),
                    "box_norm": [round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)]})
    return out, (w, h)


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=4)
    ap.add_argument("--every", type=float, default=15, help="seconds between pairs")
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--conf", type=float, default=0.25)
    args = ap.parse_args()
    try:
        cam = load_camera()
    except SecretError as e:
        print(f"ERROR: {e}")
        return 2
    from ultralytics import YOLO  # imported after the env settings above

    model = YOLO(args.model)
    folder = SAMPLES / f"{now_local():%Y-%m-%d}" / "scale"
    pairs = []
    for i in range(args.pairs):
        if i:
            time.sleep(args.every)
        stamp = now_local().strftime("%H-%M-%S")
        got = grab_pair(cam, folder, stamp)
        if len(got) < 2:
            pairs.append({"at": stamp, "error": f"frames: {sorted(got)}"})
            continue
        p101, size101 = people(model, got[101], args.conf)
        p102, size102 = people(model, got[102], args.conf)
        matched = []
        for a in p101:
            best = max(p102, key=lambda b: iou(a["box_norm"], b["box_norm"]), default=None)
            score = iou(a["box_norm"], best["box_norm"]) if best else 0.0
            matched.append({"box_norm_101": a["box_norm"], "h_101": a["h_px"], "conf_101": a["conf"],
                            "h_102": best["h_px"] if score >= 0.3 else None,
                            "conf_102": best["conf"] if score >= 0.3 else None, "iou": round(score, 2)})
        pairs.append({"at": stamp, "size_101": size101, "size_102": size102,
                      "people_101": len(p101), "people_102": len(p102), "people": matched})
        print(f"{stamp}: 101 {len(p101)} people, 102 {len(p102)} people", flush=True)
    write_json(OUT / f"compare_scale_{now_local():%Y-%m-%d_%H-%M}.json", {"model": Path(args.model).name,
               "device": "cpu", "conf": args.conf, "pairs": pairs})
    for p in pairs:
        for m in p.get("people", []):
            print(p["at"], m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
