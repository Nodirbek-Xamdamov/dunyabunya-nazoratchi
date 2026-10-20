"""Desk zones: which detected people count as "at the desk".

A zone is a polygon stored as fractions of the frame (0..1). A person is at a desk when the anchor
point of their box lies inside the polygon and the detection confidence is at least `desk_conf`.
The anchor is the box centre or a point in its upper part, never the feet: the desk hides the legs.
A person whose anchor moved more than `max_move` box heights within the last `move_window_s`
seconds is walking, not sitting, and does not count (the track ID links the frames).
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels of the full frame
    conf: float
    track_id: int = -1


def point_in_polygon(x: float, y: float, poly: list[list[float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def anchor(box: tuple[float, float, float, float], mode: str = "center") -> tuple[float, float]:
    x1, y1, x2, y2 = box
    if mode == "upper":
        return (x1 + x2) / 2, y1 + 0.3 * (y2 - y1)
    return (x1 + x2) / 2, (y1 + y2) / 2


def centroid(poly: list[list[float]]) -> tuple[float, float]:
    return sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)


def roi_for(zones: list[list[list[float]]], margin: float | None, width: int, height: int):
    """Pixel box around all zones plus `margin` (fraction of that box) - the part the model looks at."""
    points = [p for zone in zones if len(zone) >= 3 for p in zone]
    if margin is None or not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    x1 = max(0.0, min(xs) - margin * w)
    y1 = max(0.0, min(ys) - margin * h)
    x2 = min(1.0, max(xs) + margin * w)
    y2 = min(1.0, max(ys) + margin * h)
    return int(x1 * width), int(y1 * height), int(round(x2 * width)), int(round(y2 * height))


class DeskMatcher:
    def __init__(self, desks: list[dict], *, desk_conf: float = 0.35, anchor_mode: str = "center",
                 max_move: float = 0.5, move_window_s: float = 2.0):
        self.desks = [d for d in desks if len(d.get("zone") or []) >= 3]
        self.desk_conf = desk_conf
        self.anchor_mode = anchor_mode
        self.max_move = max_move
        self.move_window_s = move_window_s
        self._history: dict[int, deque] = defaultdict(deque)

    def match(self, t: float, detections: list[Detection], width: int, height: int) -> dict[int, Detection | None]:
        """For every desk id: the person at it (best confidence) or None."""
        result: dict[int, Detection | None] = {d["id"]: None for d in self.desks}
        for det in detections:
            ax, ay = anchor(det.box, self.anchor_mode)
            nx, ny = ax / width, ay / height
            if det.track_id >= 0 and self._is_walking(t, det, ax, ay):
                continue
            if det.conf < self.desk_conf:
                continue
            inside = [d for d in self.desks if point_in_polygon(nx, ny, d["zone"])]
            if not inside:
                continue
            desk = min(inside, key=lambda d: (centroid(d["zone"])[0] - nx) ** 2 + (centroid(d["zone"])[1] - ny) ** 2)
            current = result[desk["id"]]
            if current is None or det.conf > current.conf:
                result[desk["id"]] = det
        self._forget(t)
        return result

    def _is_walking(self, t: float, det: Detection, ax: float, ay: float) -> bool:
        hist = self._history[det.track_id]
        hist.append((t, ax, ay))
        while hist and hist[0][0] < t - self.move_window_s:
            hist.popleft()
        box_h = max(1.0, det.box[3] - det.box[1])
        moved = max(((x - ax) ** 2 + (y - ay) ** 2) ** 0.5 for _, x, y in hist)
        return moved > self.max_move * box_h

    def _forget(self, t: float) -> None:
        for track_id in [k for k, h in self._history.items() if not h or h[-1][0] < t - 60]:
            del self._history[track_id]
