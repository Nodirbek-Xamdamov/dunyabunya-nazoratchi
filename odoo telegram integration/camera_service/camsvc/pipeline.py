"""Frame -> people -> desks -> intervals in camera.db. Shared by the live service and clip replays.

The model only looks at the part of the frame around the desk zones (plus `roi_margin`); with no
zones it looks at the whole frame. ByteTrack links the frames of one stream (persist=True); the
desk logic itself lives in zones.py and presence.py.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from pathlib import Path

import cv2
import numpy as np

from camsvc import db, runtime
from camsvc.presence import Closed, DeskPresence, Opened, PresenceParams, Seen
from camsvc.zones import DeskMatcher, Detection, centroid, roi_for

TRACKER = runtime.SERVICE_DIR / "config" / "bytetrack_desk.yaml"
TRACK_MEMORY_S = 30.0  # a hidden person keeps the same track ID this long (monitor, someone walking past)
LIVE_DIR = Path(os.environ.get("CAMERA_LIVE_DIR") or runtime.SERVICE_DIR / "data" / "live")

GREEN, YELLOW, GREY, WHITE = (60, 200, 60), (0, 200, 255), (160, 160, 160), (255, 255, 255)


def presence_params(s: dict) -> PresenceParams:
    return PresenceParams(sit_window_s=float(s["sit_window_s"]), sit_ratio=float(s["sit_ratio"]),
                          away_grace_s=float(s["away_grace_s"]), gap_s=float(s["gap_s"]))


def tracker_config(fps: float, out_dir: Path | None = None) -> str:
    """bytetrack_desk.yaml with track_buffer (counted in frames) set for `fps`, so 30 s stays 30 s at any speed."""
    frames = max(30, round(TRACK_MEMORY_S * fps))
    out_dir = out_dir or runtime.SERVICE_DIR / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"bytetrack_desk_{frames}.yaml"
    text = re.sub(r"(?m)^track_buffer:.*$", f"track_buffer: {frames}", TRACKER.read_text(encoding="utf-8"))
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)  # a replay running at the same time never reads a half-written file
    return str(path)


def hhmm(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60}:{minutes % 60:02d}"


class Pipeline:
    def __init__(self, conn, settings: dict, desks: list[dict], *, source: str = "live"):
        from ultralytics import YOLO

        self.conn, self.settings, self.source = conn, settings, source
        self.desks = {d["id"]: d for d in desks if len(d.get("zone") or []) >= 3}
        self.model = YOLO(str(runtime.MODELS_DIR / settings["model"]))
        self.tracker = tracker_config(float(settings["target_fps"]))
        self.matcher = DeskMatcher(list(self.desks.values()), desk_conf=float(settings["desk_conf"]),
                                   anchor_mode=str(settings.get("anchor", "center")))
        params = presence_params(settings)
        self.presence = {desk_id: DeskPresence(params) for desk_id in self.desks}
        self.interval_ids: dict[int, int] = {}
        self.last: dict | None = None
        self._last_t: float | None = None

    def resume_open(self, now: float) -> None:
        """Intervals left open by the previous run: continue them after a short stop, else close them."""
        gap = float(self.settings["gap_s"])
        for row in db.open_intervals(self.conn, self.source):
            machine = self.presence.get(row["desk_id"])
            if machine is not None and now - row["last_seen_ts"] <= gap:
                machine.resume(row["start_ts"], row["last_seen_ts"])
                self.interval_ids[row["desk_id"]] = row["id"]
            else:
                db.close_interval(self.conn, row["id"], row["last_seen_ts"], "no_data")

    def process(self, image: np.ndarray, t: float) -> dict:
        height, width = image.shape[:2]
        if self._last_t is not None and t - self._last_t > float(self.settings["gap_s"]):
            db.event(self.conn, "info", f"{t - self._last_t:.0f} s kadr yo'q edi (ma'lumot yo'q)", ts=t)
        self._last_t = t
        roi = roi_for([d["zone"] for d in self.desks.values()], self.settings.get("roi_margin"), width, height)
        x0, y0 = (roi[0], roi[1]) if roi else (0, 0)
        crop = image[roi[1]:roi[3], roi[0]:roi[2]] if roi else image
        started = time.perf_counter()
        result = self.model.track(crop, persist=True, tracker=self.tracker, classes=[0], imgsz=int(self.settings["imgsz"]),
                                  half=True, device=0, verbose=False)[0]
        infer_ms = (time.perf_counter() - started) * 1000
        detections = []
        boxes = result.boxes
        if boxes is not None and len(boxes):
            ids = boxes.id.int().tolist() if boxes.id is not None else [-1] * len(boxes)
            for (x1, y1, x2, y2), conf, tid in zip(boxes.xyxy.tolist(), boxes.conf.tolist(), ids):
                detections.append(Detection((x1 + x0, y1 + y0, x2 + x0, y2 + y0), conf, tid))
        matched = self.matcher.match(t, detections, width, height, roi)
        for desk_id, det in matched.items():
            for event in self.presence[desk_id].update(t, det is not None):
                self._store(desk_id, event)
        self.last = {"t": t, "roi": roi, "detections": detections, "matched": matched, "infer_ms": infer_ms}
        return self.last

    def idle(self, t: float) -> None:
        for desk_id, machine in self.presence.items():
            for event in machine.idle(t):
                self._store(desk_id, event)

    def stop(self, *, close: bool) -> None:
        """close=True at the end of a clip; the live service leaves intervals open for a quick restart."""
        if close:
            for desk_id, machine in self.presence.items():
                for event in machine.stop():
                    self._store(desk_id, event)

    def desk_states(self, t: float) -> list[dict]:
        states = []
        for desk_id, desk in self.desks.items():
            machine = self.presence[desk_id]
            seen_now = bool(self.last and self.last["matched"].get(desk_id) is not None)
            away = machine.away_for(t)
            if machine.sitting and (seen_now or (away is not None and away < 5)):
                state = "sitting"
            elif machine.sitting:
                state = "hidden"  # interval still open, person not seen for `away` seconds
            else:
                state = "away"
            states.append({"desk_id": desk_id, "desk": desk["name"], "employee": desk.get("employee"),
                           "state": state, "since": machine.start, "away_s": away})
        return states

    def _store(self, desk_id: int, event) -> None:
        if isinstance(event, Opened):
            employee = self.desks[desk_id].get("employee_id")
            self.interval_ids[desk_id] = db.open_interval(self.conn, desk_id, employee, event.start, self.source)
        elif isinstance(event, Seen):
            if (interval := self.interval_ids.get(desk_id)) is not None:
                db.touch_interval(self.conn, interval, event.last_seen)
        elif isinstance(event, Closed):
            if (interval := self.interval_ids.pop(desk_id, None)) is not None:
                db.close_interval(self.conn, interval, event.end, event.reason)


def draw(image: np.ndarray, pipeline: Pipeline, t: float, tz=None) -> np.ndarray:
    """Zones coloured by state, people boxes, the time. Only the newest frame is ever kept."""
    out = image.copy()
    height, width = out.shape[:2]
    last = pipeline.last or {}
    states = {s["desk_id"]: s for s in pipeline.desk_states(t)}
    if last.get("roi"):
        x1, y1, x2, y2 = last["roi"]
        cv2.rectangle(out, (x1, y1), (x2, y2), (255, 200, 0), 1)
    for det in last.get("detections", []):
        strong = det.conf >= pipeline.matcher.desk_conf
        p1, p2 = (int(det.box[0]), int(det.box[1])), (int(det.box[2]), int(det.box[3]))
        cv2.rectangle(out, p1, p2, WHITE if strong else GREY, 2 if strong else 1)
        cv2.putText(out, f"{det.conf:.2f}", (p1[0] + 4, p1[1] + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, WHITE, 1)
    for desk_id, desk in pipeline.desks.items():
        state = states[desk_id]
        colour = {"sitting": GREEN, "hidden": YELLOW}.get(state["state"], GREY)
        pts = np.array([[int(x * width), int(y * height)] for x, y in desk["zone"]], np.int32)
        overlay = out.copy()
        cv2.fillPoly(overlay, [pts], colour)
        out = cv2.addWeighted(overlay, 0.18, out, 0.82, 0)
        cv2.polylines(out, [pts], True, colour, 3)
        name = desk.get("employee") or desk["name"]
        if state["state"] == "sitting":
            text = f"{name}: o'tiribdi {hhmm(t - state['since'])}"
        elif state["state"] == "hidden":
            text = f"{name}: ko'rinmayapti {int(state['away_s'] // 60)} daq"
        else:
            text = f"{name}: yo'q"
        cx, cy = centroid(desk["zone"])
        org = (max(5, int(cx * width) - 120), max(30, int(min(p[1] for p in desk["zone"]) * height) - 12))
        cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 5)
        cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.9, colour, 2)
    stamp = dt.datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d %H:%M:%S")
    cv2.rectangle(out, (0, height - 40), (width, height), (0, 0, 0), -1)
    cv2.putText(out, stamp, (12, height - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.9, WHITE, 2)
    return out


def write_live(image: np.ndarray, annotated: np.ndarray, t: float, source: str = "live") -> None:
    """The dashboard's view of the camera: newest annotated frame, newest clean frame (for drawing zones)
    and when it was taken. Each file is replaced in place; no history is kept."""
    write_jpeg(LIVE_DIR / "latest.jpg", annotated)
    write_jpeg(LIVE_DIR / "latest_raw.jpg", image, quality=85)
    height, width = image.shape[:2]
    meta = {"ts": t, "width": width, "height": height, "source": source}
    tmp = LIVE_DIR / "latest.json.tmp"
    tmp.write_text(json.dumps(meta), encoding="utf-8")
    for _ in range(5):
        try:
            os.replace(tmp, LIVE_DIR / "latest.json")
            return
        except PermissionError:
            time.sleep(0.05)


def write_jpeg(path: Path, image: np.ndarray, quality: int = 80) -> bool:
    """Atomic replace; a reader holding the old file open makes the replace fail, so retry briefly."""
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data.tobytes())
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.05)
    return False
