"""W4-01: continuous stream test (default 30 minutes) on one channel, with reconnects.

ffmpeg copies the stream without decoding (-c copy -f framecrc), Python timestamps each
packet on arrival. A break = ffmpeg exits or no packet for --stall seconds; then it
reconnects (1, 2, 4 ... 30 s) and the break is counted.
Usage: python stability.py [--channel 102] [--minutes 30]
Result: intake/out/stability_<channel>_<time>.json and a per-minute CSV.
"""
from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

from camlib import FFMPEG, OUT, SecretError, load_camera, now_local, rtsp_input, write_json


class Run:
    def __init__(self):
        self.lock = threading.Lock()
        self.last = None  # monotonic time of the last packet
        self.arrivals: list[float] = []
        self.warnings: list[str] = []


def pump_stdout(proc, run: Run):
    for line in proc.stdout:
        if line.startswith("#") or not line.strip():
            continue
        t = time.monotonic()
        with run.lock:
            run.arrivals.append(t)
            run.last = t


def pump_stderr(proc, run: Run, cam):
    for line in proc.stderr:
        line = cam.mask(line.strip())
        if line:
            with run.lock:
                if len(run.warnings) < 200:
                    run.warnings.append(f"{now_local():%H:%M:%S} {line[:200]}")


class Guard:
    """Stops the test as soon as a watched (legacy service) log gets a new line."""

    def __init__(self, paths: list[str]):
        self.paths = [Path(p) for p in paths]
        self.sizes = {p: p.stat().st_size if p.exists() else 0 for p in self.paths}

    def check(self) -> str | None:
        for p in self.paths:
            size = p.stat().st_size if p.exists() else 0
            if size > self.sizes[p]:
                with p.open("rb") as f:
                    f.seek(self.sizes[p])
                    new = f.read().decode("utf-8", "replace").strip()
                self.sizes[p] = size
                if new:
                    return f"{p.name}: {new.splitlines()[0][:200]}"
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", type=int, default=102)
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--stall", type=float, default=10, help="seconds without packets = break")
    ap.add_argument("--guard-logs", nargs="*", default=[], help="stop at once when one of these logs grows")
    args = ap.parse_args()
    guard = Guard(args.guard_logs)
    guard_hit = None
    try:
        cam = load_camera()
    except SecretError as e:
        print(f"ERROR: {e}")
        return 2

    started = now_local()
    t0 = time.monotonic()
    end = t0 + args.minutes * 60
    run = Run()
    breaks: list[dict] = []
    connects = 0
    backoff = 1.0
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostats", *rtsp_input(cam.rtsp(args.channel)),
           "-map", "0:v:0", "-c", "copy", "-f", "framecrc", "-"]

    while time.monotonic() < end:
        connects += 1
        conn_at = time.monotonic()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace")
        threads = [threading.Thread(target=pump_stdout, args=(proc, run), daemon=True),
                   threading.Thread(target=pump_stderr, args=(proc, run, cam), daemon=True)]
        for th in threads:
            th.start()
        reason = None
        while time.monotonic() < end:
            time.sleep(0.5)
            guard_hit = guard.check()
            if guard_hit:
                print(f"{now_local():%H:%M:%S} STOP, legacy log changed: {guard_hit}", flush=True)
                break
            if proc.poll() is not None:
                reason = f"ffmpeg exit {proc.returncode}"
                break
            with run.lock:
                last = run.last if run.last and run.last >= conn_at else conn_at
            if time.monotonic() - last > args.stall:
                reason = f"no packets {args.stall:.0f} s"
                break
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for th in threads:
            th.join(timeout=5)
        if reason is None or guard_hit:
            break  # test time is over, or stopped by the guard
        with run.lock:
            got_any = run.last is not None and run.last >= conn_at
            if got_any:
                backoff = 1.0
        if connects == 1 and not got_any:
            with run.lock:
                tail = run.warnings[-5:]
            print("ERROR: first connection gave no packets, stopping (no retries, to avoid a camera lockout).")
            print("\n".join(tail))
            return 1
        breaks.append({"at": now_local().isoformat(timespec="seconds"),
                       "after_s": round(time.monotonic() - t0, 1), "reason": reason})
        print(f"{now_local():%H:%M:%S} break: {reason}; reconnect in {backoff:.0f} s", flush=True)
        time.sleep(min(backoff, max(0.0, end - time.monotonic())))
        backoff = min(backoff * 2, 30)

    total_s = time.monotonic() - t0
    arr = run.arrivals
    gaps = [b - a for a, b in zip(arr, arr[1:])]
    per_min = [0] * (int(total_s // 60) + 1)
    for t in arr:
        per_min[int((t - t0) // 60)] += 1
    med = statistics.median(gaps) if gaps else None
    result = {
        "channel": args.channel,
        "started": started.isoformat(timespec="seconds"),
        "finished": now_local().isoformat(timespec="seconds"),
        "duration_min": round(total_s / 60, 2),
        "stopped_by_guard": guard_hit,
        "connections": connects,
        "breaks": len(breaks),
        "break_list": breaks,
        "packets": len(arr),
        "fps_avg": round(len(arr) / total_s, 2) if total_s else None,
        "gap_median_ms": round(med * 1000, 1) if med else None,
        "gap_p99_ms": round(sorted(gaps)[int(len(gaps) * 0.99)] * 1000, 1) if gaps else None,
        "gap_max_ms": round(max(gaps) * 1000, 1) if gaps else None,
        "gaps_over_1s": sum(g > 1 for g in gaps),
        "gaps_over_5s": sum(g > 5 for g in gaps),
        "minutes_low": [i for i, n in enumerate(per_min[:-1]) if med and n < 0.9 * 60 / med],
        "ffmpeg_warnings": len(run.warnings),
        "ffmpeg_warning_tail": run.warnings[-10:],
    }
    stamp = started.strftime("%Y-%m-%d_%H-%M")
    write_json(OUT / f"stability_{args.channel}_{stamp}.json", result)
    (OUT / f"stability_{args.channel}_{stamp}_per_minute.csv").write_text(
        "minute,packets\n" + "".join(f"{i},{n}\n" for i, n in enumerate(per_min)), encoding="utf-8")
    print({k: v for k, v in result.items() if k not in ("break_list", "ffmpeg_warning_tail")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
