"""Day numbers for each employee from the camera intervals (pure functions, no Streamlit).

Definitions:
- Keldi: start of the employee's first desk interval of the day. Ketdi: end of the last one; while that
  interval is still open the person is in the room ("hali xonada").
- Chiqish (outing): the gap between two intervals of the same day. The camera service already keeps
  absences shorter than the "turdi" wait inside an interval, so they are not outings.
- Tashqarida: length of the outings minus the time without data (an outage between two intervals is not
  time outside). A gap counts as an outing when at least a minute of it had data.
- Ma'lumot yo'q: minutes of the working hours in which the service analysed fewer than half of the frames
  it should have (camera or service down); such time is neither in nor out.
The employee is the one fixed to the desk (cam_desks.employee_id); camera tracker IDs play no part here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo

import pandas as pd

OK_SHARE = 0.5  # a minute counts as covered when at least half of the expected frames were analysed


@dataclass(frozen=True)
class Outing:
    left: float  # chiqdi
    back: float  # qaytdi
    outside_s: float  # without the minutes that had no data
    no_data_s: float


@dataclass
class EmployeeDay:
    employee_id: int
    employee: str
    desk: str
    day: date
    segments: list[tuple[float, float]] = field(default_factory=list)
    outings: list[Outing] = field(default_factory=list)
    still_in: bool = False  # the last interval is still open
    no_data_s: float = 0.0

    @property
    def first(self) -> float | None:
        return self.segments[0][0] if self.segments else None

    @property
    def last(self) -> float | None:
        return self.segments[-1][1] if self.segments else None

    @property
    def outside_s(self) -> float:
        return sum(o.outside_s for o in self.outings)


def day_bounds(day: date, tz: tzinfo) -> tuple[float, float]:
    start = datetime.combine(day, time(0, 0), tzinfo=tz)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def at(day: date, hhmm: str, tz: tzinfo) -> float:
    hours, minutes = (int(part) for part in hhmm.split(":"))
    return datetime.combine(day, time(hours, minutes), tzinfo=tz).timestamp()


def merge(segments: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(segments):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def subtract(span: tuple[float, float], holes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Parts of `span` not covered by any of `holes` (holes merged and sorted)."""
    pieces, cursor = [], span[0]
    for start, end in holes:
        if end <= cursor or start >= span[1]:
            continue
        if start > cursor:
            pieces.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < span[1]:
        pieces.append((cursor, span[1]))
    return pieces


def is_workday(employee: dict, day: date) -> bool:
    days = {int(x) for x in str(employee.get("workdays") or "").split(",") if x.strip().isdigit()}
    return day.isoweekday() in days


def no_data_periods(cov: pd.DataFrame, window: tuple[float, float], now_ts: float) -> list[tuple[float, float]]:
    """Minutes of `window` (up to now) without enough analysed frames, merged into periods."""
    start, end = window[0], min(window[1], now_ts)
    if end <= start:
        return []
    good = set()
    for row in cov.itertuples(index=False):
        if row.expected_frames > 0 and row.processed_frames >= OK_SHARE * row.expected_frames:
            good.add(int(row.minute_ts))
    bad = []
    minute = int(start // 60 * 60)
    while minute < end:
        if minute not in good:
            bad.append((max(minute, start), min(minute + 60, end)))
        minute += 60
    return merge(bad)


def coverage_share(window: tuple[float, float], gaps: list[tuple[float, float]], now_ts: float) -> float | None:
    span = min(window[1], now_ts) - window[0]
    if span <= 0:
        return None
    missing = sum(overlap(g, (window[0], min(window[1], now_ts))) for g in gaps)
    return max(0.0, 1 - missing / span)


def work_window(employees: list[dict], day: date, tz: tzinfo) -> tuple[float, float] | None:
    """Union of the active employees' working hours on `day` (earliest start to latest end)."""
    spans = [(at(day, e["work_start"], tz), at(day, e["work_end"], tz)) for e in employees
             if e.get("active", 1) and is_workday(e, day)]
    if not spans:
        return None
    return min(s for s, _ in spans), max(e for _, e in spans)


def employee_day(employee: dict, rows: pd.DataFrame, day: date, tz: tzinfo,
                 gaps: list[tuple[float, float]]) -> EmployeeDay:
    day_start, day_end = day_bounds(day, tz)
    clipped = [(max(s, day_start), min(e, day_end)) for s, e in zip(rows["start_ts"], rows["end_ts"])]
    segments = merge(clipped)
    desks = sorted({str(d) for d in rows["desk"].dropna()}) if len(rows) else []
    result = EmployeeDay(employee["id"], employee["name"], ", ".join(desks), day, segments)
    for i in range(len(segments) - 1):
        left, back = segments[i][1], segments[i + 1][0]
        outside = sum(e - s for s, e in subtract((left, back), gaps))
        if outside >= 60:
            result.outings.append(Outing(left, back, outside, (back - left) - outside))
    if segments and "open" in rows and len(rows):
        open_ends = rows.loc[rows["open"].astype(bool), "end_ts"]
        result.still_in = any(min(e, day_end) >= segments[-1][1] for e in open_ends)
    work = (at(day, employee["work_start"], tz), at(day, employee["work_end"], tz))
    result.no_data_s = sum(overlap(g, work) for g in gaps)
    return result


def day_report(employees: list[dict], intervals: pd.DataFrame, cov: pd.DataFrame, day: date, tz: tzinfo,
               now_ts: float, clip: bool = False) -> tuple[list[EmployeeDay], list[tuple[float, float]], float | None]:
    """Rows for every active employee (and anyone with intervals that day), the no-data periods inside the
    working hours, and the share of the working hours that had data."""
    if clip:  # a test clip: the window is the part of the day the clip covers
        minutes = cov["minute_ts"] if len(cov) else pd.Series(dtype=float)
        window = (float(minutes.min()), float(minutes.max()) + 60) if len(minutes) else None
    else:
        window = work_window(employees, day, tz)
    gaps = no_data_periods(cov, window, now_ts) if window else []
    share = coverage_share(window, gaps, now_ts) if window else None
    known = {e["id"]: e for e in employees}
    shown = [e for e in employees if e.get("active", 1)]
    for emp_id in intervals["employee_id"].dropna().astype(int).unique() if len(intervals) else []:
        if emp_id in known and known[emp_id] not in shown:
            shown.append(known[emp_id])
    rows = [employee_day(e, intervals[intervals["employee_id"] == e["id"]], day, tz, gaps) for e in shown]
    return rows, gaps, share


def hhmm(seconds: float | None) -> str:
    """Duration as H:MM (the way people say working hours)."""
    if seconds is None:
        return "—"
    minutes = int(round(abs(seconds) / 60))
    return f"{'-' if seconds < 0 else ''}{minutes // 60}:{minutes % 60:02d}"


def clock(ts: float | None, tz: tzinfo, seconds: bool = False) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S" if seconds else "%H:%M") if ts else "—"
