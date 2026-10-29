"""Record test clips from the camera (stage 0): video only, stream copy, into test_clips/.

    python camera_service\\tools\\record_clip.py --probe
    python camera_service\\tools\\record_clip.py --minutes 60 --channels 101 102

Each clip gets a .json next to it with the server's UTC time when ffmpeg was started
and when the first bytes reached the file (about +-2 s), so the clip can later be
replayed on the real clock. If the stream drops, a new clip is started for the time
left. ffmpeg output is masked before it is shown or logged (the URL holds the password).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camsvc.config import CLIPS_DIR, LOGS_DIR, Camera, ConfigError, camera_from_env, mask  # noqa: E402

FFMPEG = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg.exe"
FFPROBE = shutil.which("ffprobe") or r"C:\ffmpeg\ffprobe.exe"
SOCKET_TIMEOUT_US = "10000000"  # 10 s without data -> ffmpeg stops, a new clip is started
MAX_CLIPS = 6

_log_lock = threading.Lock()
_log_file = None


def log(message: str) -> None:
    line = f"{dt.datetime.now():%H:%M:%S} {message}"
    with _log_lock:
        print(line, flush=True)
        if _log_file is not None:
            _log_file.write(line + "\n")
            _log_file.flush()


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(moment: dt.datetime | None) -> str | None:
    return moment.isoformat(timespec="milliseconds") if moment else None


def probe(camera: Camera, channel: int) -> dict:
    cmd = [FFPROBE, "-hide_banner", "-v", "error", "-rtsp_transport", "tcp", "-timeout", SOCKET_TIMEOUT_US,
           "-select_streams", "v:0",
           "-show_entries", "stream=codec_name,profile,width,height,r_frame_rate,avg_frame_rate",
           "-of", "json", camera.url(channel)]
    started = time.monotonic()
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
    except subprocess.TimeoutExpired:
        return {"channel": channel, "ok": False, "error": "40 s ichida javob kelmadi"}
    info: dict = {"channel": channel, "seconds": round(time.monotonic() - started, 1)}
    try:
        streams = json.loads(out.stdout or "{}").get("streams", [])
    except json.JSONDecodeError:
        streams = []
    if out.returncode != 0 or not streams:
        info.update(ok=False, error=mask(out.stderr.strip() or f"exit {out.returncode}", camera)[-400:])
    else:
        info.update(ok=True, **streams[0])
    return info


def file_info(path: Path) -> dict:
    cmd = [FFPROBE, "-hide_banner", "-v", "error", "-show_entries",
           "format=duration,size,bit_rate:stream=codec_name,width,height,avg_frame_rate", "-of", "json", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        data = json.loads(out.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    info = dict(data.get("format", {}))
    if data.get("streams"):
        info.update(data["streams"][0])
    return info


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class Recorder(threading.Thread):
    def __init__(self, camera: Camera, channel: int, seconds: float, probe_info: dict):
        super().__init__(name=f"rec{channel}", daemon=True)
        self.camera, self.channel, self.seconds, self.probe_info = camera, channel, seconds, probe_info
        self.clips: list[dict] = []

    def run(self) -> None:
        deadline = time.monotonic() + self.seconds
        for _ in range(MAX_CLIPS):
            left = deadline - time.monotonic()
            if left < 10:
                break
            self.clips.append(self._record(left))
            if deadline - time.monotonic() >= 10:
                log(f"[{self.channel}] oqim uzildi, 5 s dan keyin yangi klip boshlanadi")
                time.sleep(5)

    def _record(self, seconds: float) -> dict:
        started = utc_now()
        path = CLIPS_DIR / f"kamera{self.channel}_{started.astimezone():%Y-%m-%d_%H-%M-%S}.mkv"
        meta_path = path.with_suffix(".json")
        meta = {"file": path.name, "channel": self.channel, "camera": self.camera.masked_url(self.channel),
                "started_utc": iso(started), "first_bytes_utc": None, "ended_utc": None,
                "planned_seconds": round(seconds), "audio": False, "probe": self.probe_info,
                "clock_note": "server soati (NTP); first_bytes_utc ~ birinchi kadr vaqti, aniqlik +-2 s"}
        write_json(meta_path, meta)
        cmd = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "warning",
               "-rtsp_transport", "tcp", "-timeout", SOCKET_TIMEOUT_US, "-i", self.camera.url(self.channel),
               "-map", "0:v:0", "-c", "copy", "-t", f"{seconds:.0f}", str(path)]
        log(f"[{self.channel}] yozish boshlandi: {path.name} ({seconds / 60:.0f} daq)")
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
        pump = threading.Thread(target=self._pump, args=(proc,), daemon=True)
        pump.start()
        while proc.poll() is None:
            if meta["first_bytes_utc"] is None and path.exists() and path.stat().st_size > 0:
                meta["first_bytes_utc"] = iso(utc_now())
                write_json(meta_path, meta)
            time.sleep(0.2)
        pump.join(timeout=5)
        meta.update(ended_utc=iso(utc_now()), ffmpeg_exit=proc.returncode)
        if path.exists():
            meta["file_info"] = file_info(path)
        write_json(meta_path, meta)
        size_mb = path.stat().st_size / 2**20 if path.exists() else 0
        duration = float(meta.get("file_info", {}).get("duration") or 0)
        log(f"[{self.channel}] tugadi: {path.name}, {size_mb:.0f} MB, {duration / 60:.1f} daq, exit {proc.returncode}")
        return meta

    def _pump(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = mask(raw.rstrip(), self.camera)
            if line:
                log(f"[{self.channel}] ffmpeg: {line}")


def main() -> int:
    global _log_file
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channels", type=int, nargs="+", default=[101, 102])
    parser.add_argument("--minutes", type=float, default=60)
    parser.add_argument("--probe", action="store_true", help="faqat oqim ma'lumotini ko'rsatish, yozmaslik")
    args = parser.parse_args()

    try:
        camera = camera_from_env()
    except ConfigError as exc:
        print(f"XATO: {exc}")
        return 2
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _log_file = open(LOGS_DIR / f"record_{dt.datetime.now():%Y-%m-%d_%H-%M-%S}.log", "a", encoding="utf-8")

    probes = {}
    for channel in args.channels:
        info = probe(camera, channel)
        probes[channel] = info
        if info["ok"]:
            log(f"[{channel}] {camera.masked_url(channel)}: {info.get('codec_name')} {info.get('width')}x"
                f"{info.get('height')}, {info.get('avg_frame_rate') or info.get('r_frame_rate')} fps,"
                f" ulanish {info['seconds']} s")
        else:
            log(f"[{channel}] {camera.masked_url(channel)}: OCHILMADI - {info['error']}")
    if args.probe:
        return 0 if all(p["ok"] for p in probes.values()) else 1

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    recorders = [Recorder(camera, ch, args.minutes * 60, probes[ch]) for ch in args.channels if probes[ch]["ok"]]
    if not recorders:
        log("Hech bir oqim ochilmadi, yozilmadi.")
        return 1
    for rec in recorders:
        rec.start()
    for rec in recorders:
        rec.join()
    log("Hammasi tugadi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
