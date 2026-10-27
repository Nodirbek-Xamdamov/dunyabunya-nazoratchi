"""Camera page model (W3-04, D-31, D-32): one permanent staff member's day from an ``api.v_staff_day`` row.

* ishda (office) = first entry -> last exit; xonada (room) = office minus every time out of the room; the
  numbers come from the view (W4 computes them), this module only arranges them for the page.
* The room intervals of the timeline are rebuilt from first entry, the exits (gaps between sessions) and
  the last exit. A day without a seen last exit keeps its last interval open (``open_end``).
* Staff are only ever named ``Hodim N`` from ``staff_no``; the view's label is not used.
* Frames: the database keeps the D-29 path ``...\\camera_events\\YYYY\\MM\\DD\\<camera>\\<file>``. Only that
  tail is used and it is read from ``Settings.frames_dir`` (the real E:\\data\\camera_events on the real
  database, synthetic pictures on the test database), so a stored path can never point anywhere else.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PureWindowsPath

from PIL import Image, UnidentifiedImageError

from core import timeutil as tu

FRAMES_FOLDER = "camera_events"
FRAME_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
FRAME_SIDE = 480
_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
_DIGITS = (4, 2, 2)  # YYYY, MM, DD


def staff_name(staff_no) -> str:
    return f"Hodim {int(staff_no)}"


def _moment(value, tz) -> datetime | None:
    """Stored-format UTC text (core.queries) or JSON ISO text -> aware local time."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, datetime):
        return value.astimezone(tz)
    text = str(value)
    if "T" in text:
        try:
            return datetime.fromisoformat(text).astimezone(tz)
        except ValueError:
            return None
    return tu.from_db(text, tz)


def _number(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _json_list(value) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


@dataclass(frozen=True)
class Exit:
    start: datetime
    end: datetime
    minutes: float | None
    inferred: bool = False  # the leaving or the coming back was only inferred (not seen)


@dataclass(frozen=True)
class Frame:
    kind: str  # in | out
    at: datetime
    path: str
    inferred: bool


@dataclass(frozen=True)
class StaffDay:
    day: date
    staff_no: int
    camera_id: str
    camera_name: str
    first_in: datetime | None
    last_out: datetime | None
    office_minutes: float | None
    room_minutes: float | None
    away_minutes: float | None
    exits: list[Exit] = field(default_factory=list)
    frames: list[Frame] = field(default_factory=list)
    in_frame: str | None = None
    out_frame: str | None = None
    uncertain: bool = False
    note: str | None = None
    ai_label: str = "AI taxmini"

    @property
    def name(self) -> str:
        return staff_name(self.staff_no)

    @property
    def inferred_frames(self) -> list[Frame]:
        return [frame for frame in self.frames if frame.inferred]

    @property
    def is_uncertain(self) -> bool:
        return self.uncertain or self.last_out is None or bool(self.inferred_frames)

    def room_intervals(self) -> list[tuple[datetime, datetime | None]]:
        """In the room: first entry -> each exit's start, each exit's end -> next one, ... -> last exit
        (None when the last exit was not seen)."""
        if self.first_in is None:
            return []
        intervals, start = [], self.first_in
        for item in self.exits:
            intervals.append((start, item.start))
            start = item.end
        intervals.append((start, self.last_out))
        return intervals


def from_row(row, tz) -> StaffDay:
    """``row``: one record of core.queries.staff_days (a pandas row or a dict)."""
    get = row.get if hasattr(row, "get") else (lambda key, default=None: getattr(row, key, default))
    frames, inferred_times = [], set()  # times of events that were only inferred
    for item in _json_list(get("frames")):
        at = _moment(item.get("frame_at") or item.get("event_at"), tz)
        if at is None:
            continue
        inferred = bool(item.get("inferred"))
        frames.append(Frame(str(item.get("kind") or ""), at, str(item.get("path") or ""), inferred))
        if inferred:
            inferred_times |= {at, _moment(item.get("event_at"), tz)}
    exits = []
    for item in _json_list(get("exits")):
        start, end = _moment(item.get("from"), tz), _moment(item.get("to"), tz)
        if start is None or end is None:
            continue
        exits.append(Exit(start, end, _number(item.get("minutes")), start in inferred_times or end in inferred_times))
    day = get("day")
    return StaffDay(
        day=day if isinstance(day, date) else date.fromisoformat(str(day)[:10]),
        staff_no=int(get("staff_no")),
        camera_id=str(get("camera_id") or ""),
        camera_name=str(get("camera_name") or get("camera_id") or ""),
        first_in=_moment(get("first_in_at"), tz),
        last_out=_moment(get("last_out_at"), tz),
        office_minutes=_number(get("office_minutes")),
        room_minutes=_number(get("room_minutes")),
        away_minutes=_number(get("away_minutes")),
        exits=sorted(exits, key=lambda item: item.start),
        frames=sorted(frames, key=lambda item: item.at),
        in_frame=get("first_in_frame") or None,
        out_frame=get("last_out_frame") or None,
        uncertain=bool(get("uncertain")),
        note=get("uncertainty_note") or None,
        ai_label=str(get("ai_label") or "AI taxmini"),
    )


def days_from_frame(frame, tz) -> list[StaffDay]:
    if frame is None or frame.empty:
        return []
    return sorted((from_row(row, tz) for _, row in frame.iterrows()), key=lambda item: (item.day, item.staff_no))


def fmt_minutes(minutes: float | None) -> str:
    return tu.fmt_duration(None if minutes is None else minutes * 60)


def fmt_time(moment: datetime | None) -> str:
    return "—" if moment is None else f"{moment:%H:%M}"


# --------------------------------------------------------------------------- frames


def frame_tail(stored: str | None) -> tuple[str, ...] | None:
    """The D-29 tail (YYYY, MM, DD, camera, file) of a stored frame path, or None if it is not one."""
    if not isinstance(stored, str) or not stored.strip():
        return None
    parts = PureWindowsPath(stored.strip()).parts
    folders = [i for i, part in enumerate(parts) if part.lower() == FRAMES_FOLDER]
    if not folders:
        return None
    tail = parts[folders[-1] + 1:]
    if len(tail) != 5 or not all(_PART.match(part) and ".." not in part for part in tail):
        return None
    if not all(part.isdigit() and len(part) == size for part, size in zip(tail[:3], _DIGITS)):
        return None
    if not tail[4].lower().endswith(FRAME_SUFFIXES):
        return None
    return tuple(tail)


def frame_file(frames_dir: Path, stored: str | None) -> Path | None:
    """The frame file under ``frames_dir`` for a stored path; None when unusable or missing."""
    tail = frame_tail(stored)
    if tail is None:
        return None
    root = Path(frames_dir).resolve()
    path = root.joinpath(*tail).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def frame_jpeg(path: Path, side: int = FRAME_SIDE) -> bytes | None:
    """A checked, scaled-down JPEG of a frame file (None if it is not a picture)."""
    try:
        with Image.open(path) as image:
            image.thumbnail((side, side))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "JPEG", quality=82, optimize=True)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        return None
    return buffer.getvalue()
