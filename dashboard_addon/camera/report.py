"""Day numbers for each employee from the camera intervals (pure functions, no Streamlit).

Definitions:
- O'tirgan: union of the employee's desk intervals inside the day. The camera service already keeps
  absences shorter than the "turdi" wait (10 minutes) inside an interval.
- Tanaffus: a gap between two intervals of the same day, minus the time without data (an outage
  between two intervals is not a break). A gap counts as a break when at least a minute of it had data.
- Namoz: the part of a break that falls into [prayer time, + prayer_window_min], at most prayer_max_min
  per prayer, only between arrival and the last time the person was seen. It is added to the work time.
- Ma'lumot yo'q: minutes of the working hours in which the service analysed fewer than half of the frames
  it should have (camera or service down); such time is neither present nor absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo

import pandas as pd

from camera import prayer

OK_SHARE = 0.5  # a minute counts as covered when at least half of the expected frames were analysed


@dataclass
class EmployeeDay:
    employee_id: int
    employee: str
    desk: str
    day: date
    segments: list[tuple[float, float]] = field(default_factory=list)
    breaks: list[tuple[float, float]] = field(default_factory=list)
    prayers: list[tuple[float, float, str]] = field(default_factory=list)
    break_count: int = 0
    sat_s: float = 0.0
    prayer_s: float = 0.0
    longest_s: float = 0.0
    required_s: float = 0.0
    no_data_s: float = 0.0
    hours: dict[int, float] = field(default_factory=dict)  # local hour -> minutes sat

    @property
    def work_s(self) -> float:
        return self.sat_s + self.prayer_s

    @property
    def diff_s(self) -> float:
        return self.work_s - self.required_s

    @property
    def first(self) -> float | None:
        return self.segments[0][0] if self.segments else None

    @property
    def last(self) -> float | None:
        return self.segments[-1][1] if self.segments else None


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


def hour_minutes(segments: list[tuple[float, float]], tz: tzinfo) -> dict[int, float]:
    result: dict[int, float] = {}
    for start, end in segments:
        cursor = start
        while cursor < end:
            local = datetime.fromtimestamp(cursor, tz)
            hour_end = (local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp()
            piece = min(end, hour_end) - cursor
            result[local.hour] = result.get(local.hour, 0.0) + piece / 60
            cursor += piece
    return result


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


def employee_day(employee: dict, rows: pd.DataFrame, day: date, tz: tzinfo, settings: dict,
                 gaps: list[tuple[float, float]]) -> EmployeeDay:
    day_start, day_end = day_bounds(day, tz)
    clipped = [(max(s, day_start), min(e, day_end)) for s, e in zip(rows["start_ts"], rows["end_ts"])]
    segments = merge(clipped)
    desks = sorted({str(d) for d in rows["desk"].dropna()}) if len(rows) else []
    result = EmployeeDay(employee["id"], employee["name"], ", ".join(desks), day, segments)
    result.sat_s = sum(e - s for s, e in segments)
    result.longest_s = max((e - s for s, e in segments), default=0.0)
    for i in range(len(segments) - 1):
        pieces = subtract((segments[i][1], segments[i + 1][0]), gaps)
        result.breaks += pieces
        result.break_count += int(sum(e - s for s, e in pieces) >= 60)
    result.hours = hour_minutes(segments, tz)
    if is_workday(employee, day):
        result.required_s = float(employee.get("required_minutes") or 0) * 60
    work = (at(day, employee["work_start"], tz), at(day, employee["work_end"], tz))
    result.no_data_s = sum(overlap(g, work) for g in gaps)
    if settings.get("prayer_credit", True) and result.breaks:
        window_s = float(settings.get("prayer_window_min", 30)) * 60
        cap_s = float(settings.get("prayer_max_min", 20)) * 60
        times = prayer.times_for(day, tz, float(settings.get("prayer_offset_min", 0)))
        for name in prayer.WORK_PRAYERS:
            moment = times[name].timestamp()
            window = (moment, moment + window_s)
            if window[0] > result.last or window[1] < result.first:
                continue
            credit = 0.0
            for gap in result.breaks:
                piece = overlap(gap, window)
                if piece <= 0 or credit >= cap_s:
                    continue
                take = min(piece, cap_s - credit)
                begin = max(gap[0], window[0])
                result.prayers.append((begin, begin + take, name))
                credit += take
            result.prayer_s += credit
    return result


def day_report(employees: list[dict], intervals: pd.DataFrame, cov: pd.DataFrame, day: date, tz: tzinfo,
               settings: dict, now_ts: float, clip: bool = False) -> tuple[list[EmployeeDay], list[tuple[float, float]],
                                                                         float | None]:
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
    rows = [employee_day(e, intervals[intervals["employee_id"] == e["id"]], day, tz, settings, gaps) for e in shown]
    return rows, gaps, share


def hhmm(seconds: float | None, signed: bool = False) -> str:
    """Duration as H:MM (the way people say working hours)."""
    if seconds is None:
        return "—"
    sign = "-" if seconds < 0 else ("+" if signed and seconds > 0 else "")
    minutes = int(round(abs(seconds) / 60))
    return f"{sign}{minutes // 60}:{minutes % 60:02d}"


def clock(ts: float | None, tz: tzinfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M") if ts else "—"
