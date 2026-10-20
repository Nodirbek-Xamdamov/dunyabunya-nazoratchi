"""Stage 2: person detector + ByteTrack on a recorded clip, written out as an annotated video.

    .venv\\Scripts\\python.exe tools\\detect_clip.py ..\\test_clips\\kamera101_....mkv --start 600 --seconds 180
        [--model models\\yolo26m.pt] [--imgsz 640] [--fps 5] [--roi x1,y1,x2,y2]

Runs on video time, not wall time: a frame is taken every 1/fps seconds of video.
Output (local only): review\\<clip>_<model>_<imgsz>.mp4 with boxes, track ID and
confidence, plus a .json with the numbers. Boxes below the desk threshold (0.35)
are drawn thin and grey: the tracker sees them, the desk logic will ignore them.
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

from camsvc import runtime  # noqa: E402,F401  (must come before ultralytics)
from camsvc.config import PROJECT_DIR  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

REVIEW_DIR = PROJECT_DIR / "review"
FFMPEG = r"C:\ffmpeg\ffmpeg.exe"
TRACKER = str(SERVICE_DIR / "config" / "bytetrack_desk.yaml")


def color_for(track_id: int) -> tuple[int, int, int]:
    rng = np.random.default_rng(track_id * 7919)
    return tuple(int(c) for c in rng.integers(60, 255, size=3))


def clip_start_utc(clip: Path) -> dt.datetime | None:
    meta = clip.with_suffix(".json")
    if not meta.exists():
        return None
    data = json.loads(meta.read_text(encoding="utf-8"))
    stamp = data.get("first_bytes_utc") or data.get("started_utc")
    return dt.datetime.fromisoformat(stamp) if stamp else None


def parse_roi(text: str | None) -> tuple[int, int, int, int] | None:
    if not text:
        return None
    x1, y1, x2, y2 = (int(v) for v in text.split(","))
    return x1, y1, x2, y2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clip", type=Path)
    parser.add_argument("--start", type=float, default=0.0, help="seconds from the clip start")
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--model", default=str(runtime.MODELS_DIR / "yolo26m.pt"))
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--desk-conf", type=float, default=0.35)
    parser.add_argument("--roi", help="x1,y1,x2,y2 - run the model only on this part of the frame")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    runtime.check()

    import torch
    from ultralytics import YOLO

    model = YOLO(args.model)
    roi = parse_roi(args.roi)
    cap = cv2.VideoCapture(str(args.clip), cv2.CAP_FFMPEG)
    if not cap.isOpened():
        print(f"video ochilmadi: {args.clip}")
        return 1
    if args.start:
        cap.set(cv2.CAP_PROP_POS_MSEC, args.start * 1000)
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    start_utc = clip_start_utc(args.clip)
    tag = f"{Path(args.model).stem}_{args.imgsz}" + ("_roi" if roi else "")
    out_path = args.out or REVIEW_DIR / f"{args.clip.stem}_{tag}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = subprocess.Popen(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{width}x{height}", "-r", f"{args.fps:g}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "27", "-pix_fmt", "yuv420p", str(out_path)],
        stdin=subprocess.PIPE)

    period_ms = 1000.0 / args.fps
    next_ms = None
    end_ms = (args.start + args.seconds) * 1000
    infer_ms: list[float] = []
    people: list[int] = []
    tracks: dict[int, dict] = {}
    processed = 0
    torch.cuda.reset_peak_memory_stats()
    while True:
        if not cap.grab():
            break
        pos = cap.get(cv2.CAP_PROP_POS_MSEC)
        if pos > end_ms:
            break
        if next_ms is None:
            next_ms = pos
        if pos + 1e-3 < next_ms:
            continue
        next_ms += period_ms
        ok, frame = cap.retrieve()
        if not ok:
            continue
        x0, y0 = 0, 0
        source = frame
        if roi:
            x0, y0, x1, y1 = roi
            source = frame[y0:y1, x0:x1]
        t0 = time.perf_counter()
        result = model.track(source, persist=True, tracker=TRACKER, classes=[0], imgsz=args.imgsz, half=True,
                             device=0, verbose=False)[0]
        elapsed = (time.perf_counter() - t0) * 1000
        if processed >= 3:  # first calls include warm-up
            infer_ms.append(elapsed)
        processed += 1
        count = 0
        boxes = result.boxes
        if roi:
            cv2.rectangle(frame, (roi[0], roi[1]), (roi[2], roi[3]), (255, 200, 0), 1)
        if boxes is not None and len(boxes):
            ids = boxes.id.int().tolist() if boxes.id is not None else [-1] * len(boxes)
            for (bx1, by1, bx2, by2), conf, tid in zip(boxes.xyxy.tolist(), boxes.conf.tolist(), ids):
                p1 = (int(bx1) + x0, int(by1) + y0)
                p2 = (int(bx2) + x0, int(by2) + y0)
                strong = conf >= args.desk_conf
                count += int(strong)
                colour = color_for(tid) if strong else (150, 150, 150)
                cv2.rectangle(frame, p1, p2, colour, 3 if strong else 1)
                label = f"ID {tid}  {conf:.2f}"
                cv2.putText(frame, label, (p1[0], max(20, p1[1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
                cv2.putText(frame, label, (p1[0], max(20, p1[1] - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, colour, 2)
                if tid >= 0:
                    info = tracks.setdefault(tid, {"first_s": pos / 1000, "frames": 0, "conf": []})
                    info["last_s"] = pos / 1000
                    info["frames"] += 1
                    info["conf"].append(conf)
        people.append(count)
        clock = ""
        if start_utc:
            clock = (start_utc + dt.timedelta(milliseconds=pos)).astimezone().strftime("%H:%M:%S")
        header = f"{clock}  video {pos / 1000:7.1f} s  {tag}  {elapsed:5.1f} ms  odam(>= {args.desk_conf}): {count}"
        cv2.rectangle(frame, (0, height - 44), (width, height), (0, 0, 0), -1)
        cv2.putText(frame, header, (12, height - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        writer.stdin.write(frame.tobytes())
    cap.release()
    writer.stdin.close()
    writer.wait()

    summary = {
        "clip": args.clip.name, "start_s": args.start, "seconds": args.seconds, "model": Path(args.model).name,
        "imgsz": args.imgsz, "fps": args.fps, "roi": roi, "frames": processed,
        "infer_ms_mean": round(statistics.fmean(infer_ms), 1) if infer_ms else None,
        "infer_ms_p95": round(sorted(infer_ms)[int(0.95 * (len(infer_ms) - 1))], 1) if infer_ms else None,
        "vram_peak_mb": round(torch.cuda.max_memory_reserved() / 2**20),
        "people_per_frame": {str(k): people.count(k) for k in sorted(set(people))},
        "track_ids": len(tracks),
        "tracks": {str(k): {"first_s": round(v["first_s"], 1), "last_s": round(v["last_s"], 1),
                            "frames": v["frames"], "conf_mean": round(statistics.fmean(v["conf"]), 2)}
                   for k, v in sorted(tracks.items())},
        "video": str(out_path),
    }
    out_path.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "tracks"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
