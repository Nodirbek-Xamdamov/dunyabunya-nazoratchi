"""Prayer times for Tashkent computed locally (no internet): the standard astronomical formulas used by
prayer-time tables (sun declination and equation of time for the day), Asr by the Hanafi rule (shadow =
2 x object + noon shadow), Fajr and Isha at 15 degrees below the horizon. Results can differ from the
official calendar by a few minutes; the «Sozlash» page shows them and has an offset setting."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, tzinfo

LAT, LON = 41.2995, 69.2401  # Tashkent
FAJR_ANGLE = ISHA_ANGLE = 15.0
RISE_SET_ANGLE = 0.833
ASR_FACTOR = 2  # Hanafi
NAMES = ("bomdod", "quyosh", "peshin", "asr", "shom", "xufton")
WORK_PRAYERS = ("peshin", "asr", "shom", "xufton")


def _sin(d: float) -> float:
    return math.sin(math.radians(d))


def _cos(d: float) -> float:
    return math.cos(math.radians(d))


def _tan(d: float) -> float:
    return math.tan(math.radians(d))


def _acos(x: float) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, x))))


def _julian(day: date) -> float:
    year, month = day.year, day.month
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day.day + b - 1524.5


def _sun(jd: float) -> tuple[float, float]:
    """Declination (degrees) and equation of time (hours)."""
    d = jd - 2451545.0
    g = (357.529 + 0.98560028 * d) % 360
    q = (280.459 + 0.98564736 * d) % 360
    lam = (q + 1.915 * _sin(g) + 0.020 * _sin(2 * g)) % 360
    e = 23.439 - 0.00000036 * d
    ra = math.degrees(math.atan2(_cos(e) * _sin(lam), _cos(lam))) / 15.0
    eqt = q / 15.0 - (ra % 24)
    if eqt > 12:
        eqt -= 24
    if eqt < -12:
        eqt += 24
    decl = math.degrees(math.asin(_sin(e) * _sin(lam)))
    return decl, eqt


def _hours(day: date, offset_hours: float) -> dict[str, float]:
    jd = _julian(day) - LON / (15 * 24)

    def noon(t: float) -> float:
        return (12 - _sun(jd + t)[1]) % 24

    def angle_time(angle: float, t: float, before_noon: bool) -> float:
        decl = _sun(jd + t)[0]
        span = _acos((-_sin(angle) - _sin(decl) * _sin(LAT)) / (_cos(decl) * _cos(LAT))) / 15.0
        return noon(t) + (-span if before_noon else span)

    def asr(t: float) -> float:
        decl = _sun(jd + t)[0]
        angle = -math.degrees(math.atan(1 / (ASR_FACTOR + _tan(abs(LAT - decl)))))
        return angle_time(angle, t, False)

    raw = {
        "bomdod": angle_time(FAJR_ANGLE, 5 / 24, True),
        "quyosh": angle_time(RISE_SET_ANGLE, 6 / 24, True),
        "peshin": noon(12 / 24),
        "asr": asr(13 / 24),
        "shom": angle_time(RISE_SET_ANGLE, 18 / 24, False),
        "xufton": angle_time(ISHA_ANGLE, 18 / 24, False),
    }
    return {name: value + offset_hours - LON / 15.0 for name, value in raw.items()}


def times_for(day: date, tz: tzinfo, offset_min: float = 0.0) -> dict[str, datetime]:
    """Local times of the five prayers (and sunrise) on `day`, shifted by `offset_min`."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=tz)
    offset_hours = midnight.utcoffset().total_seconds() / 3600
    return {name: midnight + timedelta(hours=value, minutes=offset_min)
            for name, value in _hours(day, offset_hours).items()}
