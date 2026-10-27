"""Process setup that must happen before ultralytics is imported.

Ultralytics keeps its settings in YOLO_CONFIG_DIR. It points into camera_service/data,
so nothing changes in the user's profile and other projects are not affected.
YOLO_OFFLINE stops its network checks (DNS lookups at import, update checks, automatic
downloads and pip installs): the weights are already in models/. check() also switches
off its telemetry ("sync": anonymous usage statistics and crash reports).
"""

from __future__ import annotations

import os
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = SERVICE_DIR / "data" / "ultralytics"
MODELS_DIR = SERVICE_DIR / "models"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("YOLO_CONFIG_DIR", str(CONFIG_DIR))
os.environ.setdefault("YOLO_OFFLINE", "True")
os.environ.setdefault("YOLO_VERBOSE", "False")
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


def check() -> None:
    from ultralytics import settings

    if settings.get("sync", True):
        settings.update({"sync": False})
    if settings.get("sync", True):
        raise RuntimeError("Ultralytics telemetry (sync) could not be switched off")
