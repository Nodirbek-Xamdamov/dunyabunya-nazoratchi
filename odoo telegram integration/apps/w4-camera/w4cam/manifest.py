"""Day manifest: <camera_dir>/<YYYY-MM-DD>/manifest.jsonl, one JSON object per line, append only.

Kept simple and stable on purpose: W0-04 imports it into `video_segments` later.
  {"v":1,"type":"segment","camera":"camera1","day":"2026-10-27","file":"20261027T020002Z.mkv",
   "start_utc":"2026-10-27T02:00:02.000Z","end_utc":"2026-10-27T02:10:00.050Z","duration_s":597.05,"bytes":2612345,
   "session":"20261027T020000Z","seq":0,"break_before":false,"gap_before_s":null,
   "end_source":"list","closed":"segment"}
  {"v":1,"type":"gap","camera":"camera1","day":"2026-10-27","from_utc":"...","to_utc":"...","duration_s":7.4,
   "reason":"ffmpeg_exit"}
end_source: "list" = from ffmpeg's segment list (exact media time), "mtime" = file's last write time
(the segment was not closed by ffmpeg: kill, stall, crash). closed: segment | stop | stall | ffmpeg_exit | recovered.
gap: a hole inside the recording window between two segments (not the time outside the window).
Lines are in write order, not always in time order: sort by start/from time. Optional "late": true marks a
line added afterwards by hand (2026-10-26: a gap lost by a test before the unannounced-hole rule existed).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1


def utc_iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def name_time(file_name: str) -> datetime | None:
    """'20261027T020002Z.mkv' -> aware UTC datetime (the moment ffmpeg opened the file)."""
    try:
        return datetime.strptime(file_name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class Manifest:
    def __init__(self, path: Path, camera: str, day: str):
        self.path, self.camera, self.day = path, camera, day
        self.files: set[str] = set()
        self.last_end: datetime | None = None
        self.window_start: datetime | None = None  # set by the recorder: start of a hole at the day's beginning
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn last line after a crash; the file is append only
                if rec.get("type") == "segment":
                    self.files.add(rec["file"])
                    end = parse_utc(rec["end_utc"])
                    self.last_end = max(self.last_end, end) if self.last_end else end

    def _append(self, rec: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def add_gap(self, start: datetime, end: datetime, reason: str) -> dict:
        rec = {"v": SCHEMA, "type": "gap", "camera": self.camera, "day": self.day, "from_utc": utc_iso(start),
               "to_utc": utc_iso(end), "duration_s": round((end - start).total_seconds(), 3), "reason": reason}
        self._append(rec)
        return rec

    def add_segment(self, file: str, start: datetime, end: datetime, size: int, session: str, seq: int,
                    break_before: bool, end_source: str, closed: str) -> dict:
        gap = round((start - self.last_end).total_seconds(), 3) if self.last_end else None
        rec = {"v": SCHEMA, "type": "segment", "camera": self.camera, "day": self.day, "file": file,
               "start_utc": utc_iso(start), "end_utc": utc_iso(end),
               "duration_s": round((end - start).total_seconds(), 3), "bytes": size, "session": session,
               "seq": seq, "break_before": break_before, "gap_before_s": gap,
               "end_source": end_source, "closed": closed}
        self._append(rec)
        self.files.add(file)
        self.last_end = max(self.last_end, end) if self.last_end else end
        return rec
