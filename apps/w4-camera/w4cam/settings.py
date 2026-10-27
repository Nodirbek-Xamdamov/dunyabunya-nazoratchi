"""Recorder settings from the shared config (config.toml + apps/w4-camera/app.toml [recorder])."""
from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from datetime import timedelta, timezone, tzinfo
from pathlib import Path

APP = "w4-camera"
APP_DIR = Path(__file__).resolve().parents[1]
ROOT = APP_DIR.parents[1]
SHARED = ROOT / "shared"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

from genesis_common import load_config  # noqa: E402  (needs the path above)


@dataclass(frozen=True)
class RecorderSettings:
    camera_id: str
    secret: str
    channel: int
    window_start_min: int  # minutes after local midnight
    window_end_min: int
    tz: tzinfo
    segment_s: int
    video_root: Path
    keep_root: Path
    retention_days: int
    stall_s: float
    socket_timeout_s: float
    backoff_max_s: float
    warn_free_gb: float
    stop_free_gb: float
    heartbeat_s: float
    state_dir: Path

    @property
    def camera_dir(self) -> Path:
        return self.video_root / self.camera_id

    def with_(self, **kw) -> "RecorderSettings":
        return replace(self, **kw)


def parse_hhmm(text: str) -> int:
    h, m = (int(x) for x in text.strip().split(":"))
    if not (0 <= m < 60 and 0 <= h * 60 + m <= 1440):
        raise ValueError(f"bad time {text!r}")
    return h * 60 + m


def parse_window(text: str) -> tuple[int, int]:
    """'07:00-22:00' -> (420, 1320). The window stays inside one day (one day = one folder)."""
    a, b = text.split("-")
    start, end = parse_hhmm(a), parse_hhmm(b)
    if end <= start:
        raise ValueError(f"window must not cross midnight: {text!r}")
    return start, end


def parse_offset(text: str) -> timezone:
    sign = -1 if text.strip().startswith("-") else 1
    h, m = (int(x) for x in text.strip().lstrip("+-").split(":"))
    return timezone(sign * timedelta(hours=h, minutes=m))


def local_tz(name: str, fallback_offset: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - Windows Python without tzdata
        return parse_offset(fallback_offset)


def load_settings() -> RecorderSettings:
    cfg = load_config(APP)
    r = cfg.get("app", {}).get("recorder", {})
    start, end = parse_window(r.get("window", "07:00-22:00"))
    return RecorderSettings(
        camera_id=r.get("camera_id", "camera1"),
        secret=r.get("secret", "camera1.txt"),
        channel=int(r.get("channel", 102)),
        window_start_min=start,
        window_end_min=end,
        tz=local_tz(cfg.get_dotted("project.timezone", "Asia/Tashkent"), r.get("utc_offset", "+05:00")),
        segment_s=int(r.get("segment_minutes", 10)) * 60,
        video_root=Path(r.get("video_root", r"E:\data\video")),
        keep_root=Path(r.get("keep_root", r"E:\data\video_keep")),
        retention_days=int(r.get("retention_days", 14)),
        stall_s=float(r.get("stall_seconds", 60)),
        socket_timeout_s=float(r.get("socket_timeout_seconds", 10)),
        backoff_max_s=float(r.get("backoff_max_seconds", 60)),
        warn_free_gb=float(r.get("warn_free_gb", 20)),
        stop_free_gb=float(r.get("stop_free_gb", 2)),
        heartbeat_s=float(r.get("heartbeat_seconds", 10)),
        state_dir=Path(r.get("state_dir", str(ROOT / "var" / APP))),
    )
