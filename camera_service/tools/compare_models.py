"""Stage 2: compare model sizes, image sizes and streams on the same moments of the test clips.

    .venv\\Scripts\\python.exe tools\\compare_models.py extract --clip101 ..\\test_clips\\kamera101_....mkv
        --clip102 ..\\test_clips\\kamera102_....mkv --start 300 --minutes 20 --every 4
    .venv\\Scripts\\python.exe tools\\compare_models.py all

extract: takes one frame every N seconds of real time from both clips (aligned by the
         clip start times) into review\\compare\\ (local, deleted with the test clips).
all:     runs every configuration in its own process (so VRAM is measured per model),
         then prints the table. The reference is the largest model on the main stream
         at 1280; there is no hand-labelled truth yet, so "recall" means agreement
         with the reference, checked by eye on sample frames.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))

from camsvc import runtime  # noqa: E402  (must come before ultralytics)
from camsvc.config import PROJECT_DIR  # noqa: E402

import cv2  # noqa: E402

WORK = PROJECT_DIR / "review" / "compare"
MAIN_W = 1920
# Rough area of the three desks in main-stream pixels, until real zones are drawn (stage 3).
DESK_REGION = (1180, 740, 1920, 1080)
ROI = (1000, 560, 1920, 1080)

REFERENCE = {"name": "ref_x_1280", "model": "yolo26x.pt", "imgsz": 1280, "stream": "101"}
CONFIGS = [
    {"name": "n_640", "model": "yolo26n.pt", "imgsz": 640, "stream": "101"},
    {"name": "s_640", "model": "yolo26s.pt", "imgsz": 640, "stream": "101"},
    {"name": "m_640", "model": "yolo26m.pt", "imgsz": 640, "stream": "101"},
    {"name": "l_640", "model": "yolo26l.pt", "imgsz": 640, "stream": "101"},
    {"name": "s_960", "model": "yolo26s.pt", "imgsz": 960, "stream": "101"},
    {"name": "m_960", "model": "yolo26m.pt", "imgsz": 960, "stream": "101"},
    {"name": "s_640_102", "model": "yolo26s.pt", "imgsz": 640, "stream": "102"},
    {"name": "m_640_102", "model": "yolo26m.pt", "imgsz": 640, "stream": "102"},
    {"name": "n_640_roi", "model": "yolo26n.pt", "imgsz": 640, "stream": "101roi"},
    {"name": "s_640_roi", "model": "yolo26s.pt", "imgsz": 640, "stream": "101roi"},
    {"name": "m_640_roi", "model": "yolo26m.pt", "imgsz": 640, "stream": "101roi"},
    {"name": "s_960_roi", "model": "yolo26s.pt", "imgsz": 960, "stream": "101roi"},
    {"name": "m_960_roi", "model": "yolo26m.pt", "imgsz": 960, "stream": "101roi"},
]


def clip_start(clip: Path) -> dt.datetime:
    data = json.loads(clip.with_suffix(".json").read_text(encoding="utf-8"))
    return dt.datetime.fromisoformat(data.get("start_utc") or data["started_utc"])  # start_utc: on-screen clock


def grab_at(cap: cv2.VideoCapture, target_ms: float, state: dict):
    """Read forward until the frame at target_ms (sequential, no seeking)."""
    frame = None
    while state["pos"] < target_ms:
        if not cap.grab():
            return None
        state["pos"] = cap.get(cv2.CAP_PROP_POS_MSEC)
        if state["pos"] >= target_ms:
            ok, frame = cap.retrieve()
            return frame if ok else None
    return frame


def cmd_extract(args) -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    s101, s102 = clip_start(args.clip101), clip_start(args.clip102)
    offset_ms = (s101 - s102).total_seconds() * 1000  # 102 time = 101 time + offset
    caps = {"101": cv2.VideoCapture(str(args.clip101)), "102": cv2.VideoCapture(str(args.clip102))}
    states = {"101": {"pos": -1.0}, "102": {"pos": -1.0}}
    index = []
    t = args.start
    end = args.start + args.minutes * 60
    while t < end:
        f101 = grab_at(caps["101"], t * 1000, states["101"])
        f102 = grab_at(caps["102"], t * 1000 + offset_ms, states["102"])
        if f101 is None or f102 is None:
            break
        name = f"{int(t):05d}"
        cv2.imwrite(str(WORK / f"{name}_101.jpg"), f101, [cv2.IMWRITE_JPEG_QUALITY, 95])
        cv2.imwrite(str(WORK / f"{name}_102.jpg"), f102, [cv2.IMWRITE_JPEG_QUALITY, 95])
        wall = (s101 + dt.timedelta(seconds=t)).astimezone().strftime("%H:%M:%S")
        index.append({"name": name, "t": t, "clock": wall})
        t += args.every
    (WORK / "index.json").write_text(json.dumps({"offset_ms": offset_ms, "frames": index}, indent=2), "utf-8")
    print(f"{len(index)} juft kadr, 102 siljishi {offset_ms:.0f} ms -> {WORK}")
    return 0


def cmd_run(args) -> int:
    runtime.check()
    import torch
    from ultralytics import YOLO

    cfg = json.loads(args.config)
    frames = json.loads((WORK / "index.json").read_text("utf-8"))["frames"]
    model = YOLO(str(runtime.MODELS_DIR / cfg["model"]))
    torch.cuda.reset_peak_memory_stats()
    out, times = [], []
    for i, item in enumerate(frames):
        stream = "102" if cfg["stream"] == "102" else "101"
        image = cv2.imread(str(WORK / f"{item['name']}_{stream}.jpg"))
        dx = dy = 0
        scale = MAIN_W / image.shape[1]
        if cfg["stream"] == "101roi":
            x1, y1, x2, y2 = ROI
            image, dx, dy = image[y1:y2, x1:x2], x1, y1
        t0 = time.perf_counter()
        r = model.predict(image, classes=[0], conf=0.1, imgsz=cfg["imgsz"], half=True, device=0, verbose=False)[0]
        torch.cuda.synchronize()
        if i >= 3:
            times.append((time.perf_counter() - t0) * 1000)
        boxes = [[round((b[0] + dx) * scale, 1), round((b[1] + dy) * scale, 1), round((b[2] + dx) * scale, 1),
                  round((b[3] + dy) * scale, 1), round(c, 3)]
                 for b, c in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist())]
        out.append({"name": item["name"], "boxes": boxes})
    result = {"config": cfg, "frames": out, "ms_mean": round(statistics.fmean(times), 2),
              "ms_p95": round(sorted(times)[int(0.95 * (len(times) - 1))], 2),
              "vram_mb": round(torch.cuda.max_memory_reserved() / 2**20)}
    (WORK / f"run_{cfg['name']}.json").write_text(json.dumps(result), "utf-8")
    print(f"{cfg['name']}: {result['ms_mean']} ms, VRAM {result['vram_mb']} MB")
    return 0


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def in_desk_region(box) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    x1, y1, x2, y2 = DESK_REGION
    return x1 <= cx <= x2 and y1 <= cy <= y2


def evaluate(run: dict, ref: dict, conf: float, ref_conf: float = 0.5) -> dict:
    ref_by = {f["name"]: [b for b in f["boxes"] if b[4] >= ref_conf and in_desk_region(b)] for f in ref["frames"]}
    tp = fp = fn = count_ok = 0
    counts = []
    for frame in run["frames"]:
        cand = [b for b in frame["boxes"] if b[4] >= conf and in_desk_region(b)]
        truth = ref_by.get(frame["name"], [])
        counts.append(len(cand))
        count_ok += int(len(cand) == len(truth))
        used = set()
        for b in sorted(cand, key=lambda x: -x[4]):
            best, best_j = 0.0, None
            for j, t in enumerate(truth):
                if j not in used and (v := iou(b, t)) > best:
                    best, best_j = v, j
            if best >= 0.4:
                tp += 1
                used.add(best_j)
            else:
                fp += 1
        fn += len(truth) - len(used)
    n = len(run["frames"])
    return {"recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "count_match": round(count_ok / n, 3) if n else None,
            "desk_people_mean": round(statistics.fmean(counts), 2) if counts else None}


def cmd_all(args) -> int:
    configs = [REFERENCE] + CONFIGS
    for cfg in configs:
        if (WORK / f"run_{cfg['name']}.json").exists() and not args.rerun:
            continue
        subprocess.run([sys.executable, __file__, "run", "--config", json.dumps(cfg)], check=True)
    ref = json.loads((WORK / f"run_{REFERENCE['name']}.json").read_text("utf-8"))
    ref_counts = [len([b for b in f["boxes"] if b[4] >= 0.5 and in_desk_region(b)]) for f in ref["frames"]]
    rows = []
    for cfg in CONFIGS:
        run = json.loads((WORK / f"run_{cfg['name']}.json").read_text("utf-8"))
        row = {"name": cfg["name"], "ms": run["ms_mean"], "ms_p95": run["ms_p95"], "vram_mb": run["vram_mb"]}
        row.update(evaluate(run, ref, args.conf))
        rows.append(row)
    report = {"frames": len(ref["frames"]), "reference": REFERENCE, "ref_ms": ref["ms_mean"], "ref_vram_mb": ref["vram_mb"],
              "ref_desk_people_mean": round(statistics.fmean(ref_counts), 2),
              "ref_desk_people_hist": {str(k): ref_counts.count(k) for k in sorted(set(ref_counts))},
              "desk_conf": args.conf, "rows": rows}
    (WORK / "report.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(f"kadrlar: {report['frames']}, etalon {REFERENCE['name']}: {report['ref_ms']} ms, VRAM {report['ref_vram_mb']} MB,"
          f" stolda odam o'rtacha {report['ref_desk_people_mean']} {report['ref_desk_people_hist']}")
    print(f"{'variant':<12}{'ms':>7}{'p95':>7}{'VRAM':>7}{'recall':>8}{'precis':>8}{'soni=':>7}{'odam':>6}")
    for r in rows:
        print(f"{r['name']:<12}{r['ms']:>7}{r['ms_p95']:>7}{r['vram_mb']:>7}{r['recall']!s:>8}{r['precision']!s:>8}"
              f"{r['count_match']!s:>7}{r['desk_people_mean']!s:>6}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract")
    p.add_argument("--clip101", type=Path, required=True)
    p.add_argument("--clip102", type=Path, required=True)
    p.add_argument("--start", type=float, default=300)
    p.add_argument("--minutes", type=float, default=20)
    p.add_argument("--every", type=float, default=4)
    p = sub.add_parser("run")
    p.add_argument("--config", required=True)
    p = sub.add_parser("all")
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument("--rerun", action="store_true")
    args = parser.parse_args()
    return {"extract": cmd_extract, "run": cmd_run, "all": cmd_all}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
