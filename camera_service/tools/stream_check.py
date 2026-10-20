"""Stage 1 check: read the camera for N minutes at target_fps and report real numbers.

    .venv\\Scripts\\python.exe tools\\stream_check.py --channel 101 --minutes 10 --fps 5 --detect models\\yolo26m.pt
    .venv\\Scripts\\python.exe tools\\stream_check.py --channel 102 --minutes 2
    .venv\\Scripts\\python.exe tools\\stream_check.py --reconnect-test

Every minute it prints: frames from the camera, processed frames, frame age (how old
the newest frame was when it was taken), CPU %, GPU %, VRAM, and with --detect the
model time and people per frame. Nothing is recorded: only the report
(logs/stream_check_*.json) and one preview frame (data/last_frame_check.jpg,
overwritten every run) are written.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import threading
import time
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))

from camsvc import runtime  # noqa: E402  (sets Ultralytics config dir and telemetry off before import)
from camsvc.config import LOGS_DIR, camera_from_env, mask  # noqa: E402
from camsvc.stream import LatestFrameReader  # noqa: E402

import cv2  # noqa: E402
import psutil  # noqa: E402

DATA_DIR = SERVICE_DIR / "data"


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


class GpuSampler(threading.Thread):
    """Samples whole-GPU utilisation and memory once a second (NVML)."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.util: list[float] = []
        self.mem_used_mb: list[float] = []
        self._stop = threading.Event()
        self.ok = False
        try:
            import pynvml

            pynvml.nvmlInit()
            self._nvml = pynvml
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.ok = True
        except Exception as exc:  # noqa: BLE001 - report and run without GPU numbers
            print(f"NVML yo'q: {exc}")

    def run(self) -> None:
        while self.ok and not self._stop.wait(1.0):
            self.util.append(float(self._nvml.nvmlDeviceGetUtilizationRates(self._handle).gpu))
            self.mem_used_mb.append(self._nvml.nvmlDeviceGetMemoryInfo(self._handle).used / 2**20)

    def take(self) -> tuple[list[float], list[float]]:
        util, mem = self.util, self.mem_used_mb
        self.util, self.mem_used_mb = [], []
        return util, mem

    def stop(self) -> None:
        self._stop.set()


def load_detector(weights: str, imgsz: int, conf: float):
    import torch
    from ultralytics import YOLO

    model = YOLO(weights)

    def detect(image):
        result = model.predict(image, classes=[0], conf=conf, imgsz=imgsz, half=True, device=0, verbose=False)[0]
        return len(result.boxes)

    return detect, torch


def run_check(args, camera) -> dict:
    url = camera.url(args.channel)
    log_lines: list[str] = []

    def log(text: str) -> None:
        line = mask(f"{dt.datetime.now():%H:%M:%S} {text}", camera)
        log_lines.append(line)
        print(line, flush=True)

    detect = torch = None
    if args.detect:
        detect, torch = load_detector(args.detect, args.imgsz, args.conf)
        log(f"model: {Path(args.detect).name}, imgsz {args.imgsz}, FP16, conf {args.conf}")

    reader = LatestFrameReader(lambda: url, log=log, name=str(args.channel)).start()
    proc = psutil.Process()
    proc.cpu_percent(None)
    cores = psutil.cpu_count(logical=True) or 1
    gpu = GpuSampler()
    gpu.start()

    period = 1.0 / args.fps
    started = time.monotonic()
    end = started + args.minutes * 60
    next_tick = started
    minute_end = started + 60
    last_seq = 0
    last_frames = 0
    totals = {"ticks": 0, "processed": 0, "no_new_frame": 0, "ages_ms": [], "infer_ms": [], "people": [],
              "cpu": [], "gpu": [], "vram_mb": [], "minutes": []}
    cur = {"processed": 0, "no_new_frame": 0, "ages_ms": [], "infer_ms": [], "people": []}
    last_frame = None
    warmed = False
    while True:
        now = time.monotonic()
        last_round = now >= end
        frame = None if last_round else reader.wait_newer(last_seq, period * 0.5)
        totals["ticks"] += 1
        if last_round:
            pass
        elif frame is None or frame.seq == last_seq:
            cur["no_new_frame"] += 1
        else:
            last_seq = frame.seq
            last_frame = frame
            age = (time.monotonic() - frame.mono) * 1000
            if detect is not None:
                t0 = time.perf_counter()
                people = detect(frame.image)
                infer = (time.perf_counter() - t0) * 1000
                if warmed:
                    cur["infer_ms"].append(infer)
                    cur["people"].append(people)
                warmed = True
            cur["processed"] += 1
            cur["ages_ms"].append(age)
        if now >= minute_end or last_round:
            util, mem = gpu.take()
            cpu = proc.cpu_percent(None)
            cam_frames = reader.stats.frames - last_frames
            last_frames = reader.stats.frames
            span = max(1e-6, now - (minute_end - 60))
            row = {
                "minute": len(totals["minutes"]) + 1,
                "camera_fps": round(cam_frames / span, 2),
                "processed_fps": round(cur["processed"] / span, 2),
                "no_new_frame_ticks": cur["no_new_frame"],
                "age_ms_mean": round(statistics.fmean(cur["ages_ms"]), 1) if cur["ages_ms"] else None,
                "age_ms_p95": round(pct(cur["ages_ms"], 0.95), 1),
                "cpu_pct_of_machine": round(cpu / cores, 1),
                "cpu_pct_one_core": round(cpu, 1),
                "gpu_util_mean": round(statistics.fmean(util), 1) if util else None,
                "gpu_mem_used_mb": round(max(mem)) if mem else None,
                "connected": reader.stats.connected,
            }
            if detect is not None:
                row["infer_ms_mean"] = round(statistics.fmean(cur["infer_ms"]), 1) if cur["infer_ms"] else None
                row["infer_ms_p95"] = round(pct(cur["infer_ms"], 0.95), 1)
                row["people_mean"] = round(statistics.fmean(cur["people"]), 2) if cur["people"] else None
                row["people_max"] = max(cur["people"]) if cur["people"] else None
                row["torch_reserved_mb"] = round(torch.cuda.max_memory_reserved() / 2**20)
            totals["minutes"].append(row)
            for key in ("processed", "no_new_frame"):
                totals[key] += cur[key]
            for key in ("ages_ms", "infer_ms", "people"):
                totals[key] += cur[key]
            totals["cpu"].append(row["cpu_pct_of_machine"])
            if row["gpu_util_mean"] is not None:
                totals["gpu"].append(row["gpu_util_mean"])
                totals["vram_mb"].append(row["gpu_mem_used_mb"])
            cur = {"processed": 0, "no_new_frame": 0, "ages_ms": [], "infer_ms": [], "people": []}
            extra = ""
            if detect is not None:
                extra = f", model {row['infer_ms_mean']} ms, odam {row['people_mean']} (max {row['people_max']})"
            log(f"{row['minute']}-daq: kamera {row['camera_fps']} kadr/s, ishlov {row['processed_fps']} kadr/s, "
                f"kadr yoshi {row['age_ms_mean']} ms (p95 {row['age_ms_p95']}), CPU {row['cpu_pct_of_machine']}% "
                f"(1 yadro {row['cpu_pct_one_core']}%), GPU {row['gpu_util_mean']}%, VRAM {row['gpu_mem_used_mb']} MB"
                + extra)
            minute_end += 60
        if last_round:
            break
        next_tick += period
        delay = next_tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        elif delay < -period:
            next_tick = time.monotonic()
    reader.stop()
    gpu.stop()
    if last_frame is not None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(DATA_DIR / "last_frame_check.jpg"), last_frame.image)
    s = reader.stats
    summary = {
        "channel": args.channel,
        "camera_url": camera.masked_url(args.channel),
        "frame_size": reader.frame_size,
        "camera_fps_reported": reader.camera_fps,
        "minutes": args.minutes,
        "target_fps": args.fps,
        "camera_frames": s.frames,
        "camera_fps_real": round(s.frames / (args.minutes * 60), 2),
        "processed_frames": totals["processed"],
        "processed_fps_real": round(totals["processed"] / (args.minutes * 60), 2),
        "ticks_without_new_frame": totals["no_new_frame"],
        # frames the camera should have sent at its own rate minus frames that arrived and decoded
        "camera_frames_missing": max(0, round(reader.camera_fps * args.minutes * 60) - s.frames),
        "arrival_gaps_over_1s": s.gaps_over_1s,
        "connects": s.connects,
        "disconnects": s.disconnects,
        "failed_connects": s.failed_connects,
        "frame_age_ms_mean": round(statistics.fmean(totals["ages_ms"]), 1) if totals["ages_ms"] else None,
        "frame_age_ms_p95": round(pct(totals["ages_ms"], 0.95), 1),
        "frame_age_ms_max": round(max(totals["ages_ms"]), 1) if totals["ages_ms"] else None,
        "cpu_pct_of_machine_mean": round(statistics.fmean(totals["cpu"]), 1) if totals["cpu"] else None,
        "cpu_cores": psutil.cpu_count(logical=True),
        "gpu_util_mean": round(statistics.fmean(totals["gpu"]), 1) if totals["gpu"] else None,
        "gpu_mem_used_mb_max": max(totals["vram_mb"]) if totals["vram_mb"] else None,
        "per_minute": totals["minutes"],
        "events": [(dt.datetime.fromtimestamp(t).strftime("%H:%M:%S"), e) for t, e in s.events],
    }
    if detect is not None:
        summary.update(
            model=Path(args.detect).name, imgsz=args.imgsz,
            infer_ms_mean=round(statistics.fmean(totals["infer_ms"]), 1) if totals["infer_ms"] else None,
            infer_ms_p95=round(pct(totals["infer_ms"], 0.95), 1),
            people_mean=round(statistics.fmean(totals["people"]), 2) if totals["people"] else None,
            torch_reserved_mb_max=round(torch.cuda.max_memory_reserved() / 2**20),
        )
    summary["log"] = log_lines
    return summary


def run_reconnect_test(args, camera) -> dict:
    """1) wrong port for 40 s, then the right one; 2) drop + wrong port for 15 s; 3) drop with the right one."""
    good = camera.url(args.channel)
    wrong = good.replace(f":{camera.port}/", ":5554/")
    state = {"url": wrong}
    events: list[str] = []
    t0 = time.monotonic()

    def log(text: str) -> None:
        line = mask(f"{dt.datetime.now():%H:%M:%S} (+{time.monotonic() - t0:5.1f} s) {text}", camera)
        events.append(line)
        print(line, flush=True)

    def frame_after(moment: float, limit: float) -> float | None:
        """Seconds from `moment` until a frame received after it (so from a new connection) is there."""
        while time.monotonic() - moment < limit:
            frame = reader.latest()
            if frame is not None and frame.mono > moment:
                return frame.mono - moment
            time.sleep(0.05)
        return None

    def report(name: str, value: float | None, limit: float) -> None:
        log(f"{name}: kadr yana {value:.1f} s da keldi" if value is not None else f"{name}: {limit:.0f} s ichida kadr kelmadi")

    reader = LatestFrameReader(lambda: state["url"], log=log, name=f"{args.channel}-sinov",
                               open_timeout_s=5).start()
    log("1-sinov: noto'g'ri port (5554), 30 s")
    time.sleep(30)
    state["url"] = good
    fixed = time.monotonic()
    log("1-sinov: to'g'ri manzil qo'yildi")
    recovered1 = frame_after(fixed, 90)
    report("1-sinov", recovered1, 90)
    time.sleep(10)

    state["url"] = wrong
    reader.drop_connection()
    log("2-sinov: ulanish uzildi va 15 s davomida manzil noto'g'ri")
    time.sleep(15)
    state["url"] = good
    fixed = time.monotonic()
    log("2-sinov: to'g'ri manzil qaytarildi")
    recovered2 = frame_after(fixed, 90)
    report("2-sinov (manzil tuzalgandan keyin)", recovered2, 90)
    time.sleep(10)

    dropped = time.monotonic()
    reader.drop_connection()
    log("3-sinov: ulanish uzildi (manzil to'g'ri)")
    recovered3 = frame_after(dropped + 0.3, 60)  # +0.3 s: a frame still being read on the old connection
    report("3-sinov (uzilgandan keyin)", None if recovered3 is None else recovered3 + 0.3, 60)
    if recovered3 is not None:
        recovered3 += 0.3
    reader.stop()
    s = reader.stats
    return {"test": "reconnect", "channel": args.channel, "recovered_after_fix_s": recovered1,
            "recovered_after_outage_s": recovered2, "recovered_after_drop_s": recovered3,
            "failed_connects": s.failed_connects, "connects": s.connects, "disconnects": s.disconnects,
            "log": events}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel", type=int, default=101)
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--fps", type=float, default=5)
    parser.add_argument("--detect", help="model weights, e.g. models\\yolo26m.pt")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.35)
    parser.add_argument("--reconnect-test", action="store_true")
    args = parser.parse_args()
    runtime.check()
    camera = camera_from_env()
    result = run_reconnect_test(args, camera) if args.reconnect_test else run_check(args, camera)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    kind = "reconnect" if args.reconnect_test else f"ch{args.channel}"
    out = LOGS_DIR / f"stream_check_{kind}_{dt.datetime.now():%Y-%m-%d_%H-%M-%S}.json"
    out.write_text(mask(json.dumps(result, ensure_ascii=False, indent=2), camera), encoding="utf-8")
    print(f"hisobot: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
