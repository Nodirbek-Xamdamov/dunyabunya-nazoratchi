r"""Single frames cut from recorded segments (no camera connection), e.g. the lighting samples after W4-02.

    cd apps\w4-camera
    python -m w4cam.frames --at "2026-10-26 18:30" --at "2026-10-26 19:00"
Times are local (project timezone). Output: intake\out\samples\<date>\<HH-MM>_<label>_ch<N>.jpg
(the same names the W4-01 sample schedule used). The frame stays on this server.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .manifest import parse_utc
from .recorder import FFMPEG
from .settings import APP_DIR, RecorderSettings, load_settings

SAMPLES = APP_DIR / "intake" / "out" / "samples"


def label(t: datetime) -> str:
    hm = t.hour * 60 + t.minute
    return "ertalab" if hm < 600 else "kunduz" if hm < 17 * 60 + 30 else "kech"


def find_segment(settings: RecorderSettings, t_local: datetime) -> tuple[Path, float] | None:
    manifest = settings.camera_dir / t_local.date().isoformat() / "manifest.jsonl"
    if not manifest.is_file():
        return None
    t = t_local.astimezone(timezone.utc)
    for line in manifest.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") != "segment":
            continue
        start, end = parse_utc(rec["start_utc"]), parse_utc(rec["end_utc"])
        if start <= t < end:
            return manifest.parent / rec["file"], (t - start).total_seconds()
    return None


def cut(segment: Path, offset_s: float, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{offset_s:.3f}", "-i", str(segment),
           "-frames:v", "1", "-q:v", "2", "-y", str(dest)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return p.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", action="append", required=True, help='local time "YYYY-MM-DD HH:MM"')
    args = ap.parse_args()
    s = load_settings()
    bad = 0
    for text in args.at:
        t = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=s.tz)
        found = find_segment(s, t)
        if not found:
            print(f"{text}: yozuv yo'q")
            bad += 1
            continue
        seg, off = found
        dest = SAMPLES / t.date().isoformat() / f"{t:%H-%M}_{label(t)}_ch{s.channel}.jpg"
        ok = cut(seg, off, dest)
        print(f"{text}: {'OK ' + str(dest) if ok else 'xato'} ({seg.name} +{off:.1f} s)")
        bad += 0 if ok else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
