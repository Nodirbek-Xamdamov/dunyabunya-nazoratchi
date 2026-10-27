r"""W4-02: real-service window test after the day's window, so daytime recording is not disturbed.

    python scripts\window_test.py --date 2026-10-26     (detached; result: var\w4-camera\window_test_<date>.txt)

1. at end+20 s: the real stop (state idle, no ffmpeg, last segment ends at the window end);
2. a clean restart while idle (loads the newest code; the task brings the recorder back within a minute);
3. a temporary window via window_override.json (end+4 .. end+7 min, expires at end+9 min by itself):
   recording must start at its start (the 07:00 case) and stop at its end (the 22:00 case);
4. after the expiry: the override file is gone, state idle, next start = the real window start tomorrow.
The real app.toml is never edited.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from w4cam.settings import load_settings  # noqa: E402

s = load_settings()
HB = s.state_dir / "recorder_heartbeat.json"


def hb() -> dict:
    for _ in range(5):
        try:
            return json.loads(HB.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            time.sleep(0.3)
    return {}


def wait_until(t: datetime) -> None:
    while datetime.now(s.tz) < t:
        time.sleep(min(5.0, max(0.2, (t - datetime.now(s.tz)).total_seconds())))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    day = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=s.tz)
    end = day + timedelta(minutes=s.window_end_min)
    out = s.state_dir / f"window_test_{args.date}.txt"
    lines: list[str] = []

    def say(msg: str) -> None:
        lines.append(f"{datetime.now(s.tz):%H:%M:%S} {msg}")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    say(f"start; real window end {end:%H:%M}")
    wait_until(end + timedelta(seconds=20))
    h = hb()
    manifest = s.camera_dir / args.date / "manifest.jsonl"
    segs = [json.loads(x) for x in manifest.read_text(encoding="utf-8").splitlines() if '"segment"' in x]
    say(f"1. real stop: state={h.get('state')} ffmpeg={h.get('ffmpeg_pid')} last segment {segs[-1]['file']} "
        f"end_utc={segs[-1]['end_utc']} closed={segs[-1]['closed']} next_start={h.get('next_start_local')}")

    old_pid = h.get("pid")
    (s.state_dir / "STOP_RECORDER").write_text("window test restart", encoding="utf-8")
    t0 = time.monotonic()
    while time.monotonic() - t0 < 150:
        time.sleep(2)
        h = hb()
        if h.get("pid") and h.get("pid") != old_pid and h.get("state") == "idle":
            break
    say(f"2. restart while idle: new pid {h.get('pid')} (old {old_pid}) after {time.monotonic() - t0:.0f} s, "
        f"state={h.get('state')}")

    w_start, w_end = end + timedelta(minutes=4), end + timedelta(minutes=7)
    until = end + timedelta(minutes=9)
    override = s.state_dir / "window_override.json"
    override.write_text(json.dumps({"window": f"{w_start:%H:%M}-{w_end:%H:%M}", "until": until.isoformat()}),
                        encoding="utf-8")
    say(f"3. override {w_start:%H:%M}-{w_end:%H:%M} until {until:%H:%M}")
    last = None
    while datetime.now(s.tz) < until + timedelta(seconds=40):
        h = hb()
        cur = (h.get("state"), bool(h.get("ffmpeg_pid")))
        if cur != last:
            say(f"   state={cur[0]} ffmpeg={'yes' if cur[1] else 'no'} file={h.get('current_file')}")
            last = cur
        time.sleep(2)
    segs = [json.loads(x) for x in manifest.read_text(encoding="utf-8").splitlines() if '"segment"' in x]
    test = [x for x in segs if datetime.fromisoformat(x["start_utc"].replace("Z", "+00:00")) >= w_start - timedelta(minutes=1)]
    for x in test:
        say(f"   segment {x['file']} {x['start_utc']} .. {x['end_utc']} ({x['duration_s']} s, {x['closed']})")
    h = hb()
    say(f"4. after expiry: override file exists={override.exists()} state={h.get('state')} "
        f"next_start={h.get('next_start_local')}")
    ok = (bool(test) and not override.exists() and h.get("state") == "idle"
          and str(h.get("next_start_local", "")).endswith(f"T{s.window_start_min // 60:02d}:"
                                                          f"{s.window_start_min % 60:02d}:00+05:00"))
    say("RESULT " + ("OK" if ok else "CHECK"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
