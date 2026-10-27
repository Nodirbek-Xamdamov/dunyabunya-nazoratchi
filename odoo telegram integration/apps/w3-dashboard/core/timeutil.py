"""Time handling: the database stores naive UTC text, the dashboard shows Asia/Tashkent time.

Period presets are computed in local time. "Previous period" is the same period shifted back by its
nominal length; for a period that is still running (e.g. today) both are cut at the same elapsed
point, so "today until 14:30" is compared with "yesterday until 14:30".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DB_FORMAT = "%Y-%m-%d %H:%M:%S.%f"

MONTHS = ("yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr")
WEEKDAYS = ("dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba")
WEEKDAYS_SHORT = ("Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya")

PRESETS = ("today", "yesterday", "last24h", "last7", "last30", "this_month", "last_month", "report", "custom")
DEFAULT_PRESET = "last7"


@dataclass(frozen=True)
class Period:
    key: str
    start: datetime  # aware, local time zone
    end: datetime  # aware, exclusive

    @property
    def start_db(self) -> str:
        return to_db(self.start)

    @property
    def end_db(self) -> str:
        return to_db(self.end)

    @property
    def length(self) -> timedelta:
        return self.end - self.start

    @property
    def days(self) -> int:
        return max(1, math.ceil(self.length / timedelta(days=1)))

    def is_open(self, now: datetime) -> bool:
        return self.end > now

    def offset_modifier(self) -> str:
        """SQLite datetime() modifier that turns stored UTC into local time (fixed offset;
        Asia/Tashkent has no daylight saving time)."""
        minutes = int(self.start.utcoffset().total_seconds() // 60)
        return f"{minutes:+d} minutes"

    def previous(self, now: datetime) -> "Period":
        length = self.length
        start = self.start - length
        end = self.start
        if self.is_open(now):
            end = min(end, start + max(now - self.start, timedelta(minutes=1)))
        return Period("previous", start, end)

    def local_days(self) -> list[date]:
        first = self.start.date()
        last = (self.end - timedelta(microseconds=1)).date()
        return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def now_local(tz: ZoneInfo) -> datetime:
    return datetime.now(tz).replace(second=0, microsecond=0)


def to_db(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(tzinfo=None).strftime(DB_FORMAT)


def from_db(value: str | None, tz: ZoneInfo) -> datetime | None:
    """Stored naive UTC text -> aware local datetime."""
    if not value:
        return None
    text = str(value)
    for fmt in (DB_FORMAT, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).astimezone(tz)
        except ValueError:
            continue
    return None


def _midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def report_day_of(moment: datetime, report_time: time) -> date:
    """The bot's report day a local moment belongs to (window: day REPORT_TIME -> next day REPORT_TIME)."""
    return (moment - timedelta(hours=report_time.hour, minutes=report_time.minute)).date()


def report_bounds(day: date, report_time: time, tz) -> tuple[datetime, datetime]:
    start = datetime.combine(day, report_time, tzinfo=tz)
    return start, start + timedelta(days=1)


def day_window(first: date, last: date, tz, key: str = "window") -> "Period":
    return Period(key, _midnight(first, tz), _midnight(last, tz) + timedelta(days=1))


def report_window_start(now: datetime, report_time: time) -> datetime:
    """Start of the report window that contains ``now``."""
    start = now.replace(hour=report_time.hour, minute=report_time.minute, second=0, microsecond=0)
    return start if start <= now else start - timedelta(days=1)


def resolve_period(key: str, now: datetime, report_time: time, custom: tuple[date, date] | None = None) -> Period:
    tz = now.tzinfo
    today = _midnight(now.date(), tz)
    if key == "today":
        return Period(key, today, today + timedelta(days=1))
    if key == "yesterday":
        return Period(key, today - timedelta(days=1), today)
    if key == "last24h":
        end = now + timedelta(minutes=1)
        return Period(key, end - timedelta(days=1), end)
    if key == "last7":
        return Period(key, today - timedelta(days=6), today + timedelta(days=1))
    if key == "last30":
        return Period(key, today - timedelta(days=29), today + timedelta(days=1))
    if key == "this_month":
        first = today.replace(day=1)
        return Period(key, first, _midnight(_add_month(first.date()), tz))
    if key == "last_month":
        first = today.replace(day=1)
        previous_first = _midnight(_add_month(first.date(), -1), tz)
        return Period(key, previous_first, first)
    if key == "report":
        current = report_window_start(now, report_time)
        return Period(key, current - timedelta(days=1), current)
    if key == "custom" and custom:
        first, last = sorted(custom)
        return Period(key, _midnight(first, tz), _midnight(last, tz) + timedelta(days=1))
    return resolve_period("today", now, report_time)


def _add_month(day: date, months: int = 1) -> date:
    month_index = day.month - 1 + months
    return date(day.year + month_index // 12, month_index % 12 + 1, 1)


# --------------------------------------------------------------------------- formatting


def fmt_int(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{int(round(value)):,}".replace(",", " ")


def fmt_pct(value: float | None, digits: int | None = None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    percent = value * 100
    if digits is None:
        digits = 0 if abs(percent) >= 10 or percent == int(percent) else 1
    return f"{percent:.{digits}f}".replace(".", ",") + "%"


def fmt_day(day: date, with_year: bool = False) -> str:
    text = f"{day.day}-{MONTHS[day.month - 1]}"
    return f"{text} {day.year}" if with_year else text


def fmt_dt(moment: datetime | None, with_year: bool = False) -> str:
    if moment is None:
        return "—"
    return f"{fmt_day(moment.date(), with_year)}, {moment:%H:%M}"


def fmt_period(period: Period) -> str:
    start, end = period.start, period.end
    if start.time() == time.min and end.time() == time.min:
        last = (end - timedelta(days=1)).date()
        if last == start.date():
            return fmt_day(start.date())
        return f"{fmt_day(start.date())} — {fmt_day(last)}"
    return f"{fmt_dt(start)} — {fmt_dt(end)}"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or (isinstance(seconds, float) and math.isnan(seconds)):
        return "—"
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} soniya"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} daq"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} soat {minutes} daq" if minutes else f"{hours} soat"
    days, hours = divmod(hours, 24)
    return f"{days} kun {hours} soat" if hours else f"{days} kun"


def fmt_ago(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return "—"
    minutes = max(0, int((now - moment).total_seconds() // 60))
    if minutes < 1:
        return "hozirgina"
    if minutes < 60:
        return f"{minutes} daqiqa oldin"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} soat oldin"
    return f"{hours // 24} kun oldin"
