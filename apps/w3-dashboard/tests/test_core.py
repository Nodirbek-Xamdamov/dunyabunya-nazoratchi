"""core: periods, formatting, metrics, risk, escaping, export sanitising, (database tests: test_data_layer.py)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO

import pandas as pd
import pytest
from openpyxl import load_workbook

from core import metrics as m_
from core import timeutil as tu
from core.export import neutralize, to_csv_bytes, to_xlsx_bytes
from core.settings import RiskConfig, get_tz, load_settings
from core.text import esc, like_pattern, top_words

TZ = get_tz("Asia/Tashkent")
NOW = datetime(2026, 10, 15, 14, 30, tzinfo=TZ)
RISK = RiskConfig(w_missed=0.35, w_rate=0.25, w_growth=0.2, w_slow=0.2, cap_missed=5, cap_rate=0.4, cap_growth=1.0,
                  cap_slow_minutes=30, min_related=3, red=70, yellow=40)


# --------------------------------------------------------------------------- time


def test_utc_storage_is_shown_in_tashkent_time():
    assert tu.from_db("2026-10-14 06:00:10.716530", TZ).strftime("%Y-%m-%d %H:%M") == "2026-10-14 11:00"
    assert tu.to_db(datetime(2026, 10, 15, 0, 0, tzinfo=TZ)) == "2026-10-14 19:00:00.000000"
    assert tu.from_db(None, TZ) is None


@pytest.mark.parametrize("key, start, end", [
    ("today", "2026-10-15 00:00", "2026-10-16 00:00"),
    ("yesterday", "2026-10-14 00:00", "2026-10-15 00:00"),
    ("last7", "2026-10-09 00:00", "2026-10-16 00:00"),
    ("last30", "2026-09-16 00:00", "2026-10-16 00:00"),
    ("this_month", "2026-10-01 00:00", "2026-11-01 00:00"),
    ("last_month", "2026-09-01 00:00", "2026-10-01 00:00"),
    ("report", "2026-10-14 11:00", "2026-10-15 11:00"),
    ("last24h", "2026-10-14 14:31", "2026-10-15 14:31"),
])
def test_presets(key, start, end):
    period = tu.resolve_period(key, NOW, time(11, 0))
    assert period.start.strftime("%Y-%m-%d %H:%M") == start
    assert period.end.strftime("%Y-%m-%d %H:%M") == end


def test_custom_range_and_report_window_before_report_time():
    period = tu.resolve_period("custom", NOW, time(11, 0), (date(2026, 10, 10), date(2026, 10, 3)))
    assert (period.start.date(), period.end.date()) == (date(2026, 10, 3), date(2026, 10, 11))
    morning = NOW.replace(hour=9)
    report = tu.resolve_period("report", morning, time(11, 0))
    assert report.start.strftime("%d %H:%M") == "13 11:00" and report.end.strftime("%d %H:%M") == "14 11:00"


def test_previous_period_same_length_and_running_period_is_cut():
    today = tu.resolve_period("today", NOW, time(11, 0))
    previous = today.previous(NOW)
    assert previous.start == datetime(2026, 10, 14, 0, 0, tzinfo=TZ)
    assert previous.end == datetime(2026, 10, 14, 14, 30, tzinfo=TZ)  # same part of yesterday
    closed = tu.resolve_period("yesterday", NOW, time(11, 0)).previous(NOW)
    assert (closed.start.day, closed.end.day) == (13, 14)


def test_formatting():
    assert tu.fmt_int(1234567) == "1 234 567"
    assert tu.fmt_pct(0.1234) == "12%" and tu.fmt_pct(0.055) == "5,5%" and tu.fmt_pct(None) == "—"
    assert tu.fmt_dt(datetime(2026, 10, 15, 11, 0, tzinfo=TZ)) == "15-oktabr, 11:00"
    assert tu.fmt_duration(45) == "45 soniya" and tu.fmt_duration(3 * 3600 + 120) == "3 soat 2 daq"
    assert tu.WEEKDAYS[0] == "dushanba" and tu.MONTHS[0] == "yanvar"
    assert tu.report_day_of(datetime(2026, 10, 15, 10, 59, tzinfo=TZ), time(11, 0)) == date(2026, 10, 14)


# --------------------------------------------------------------------------- metrics


def test_change_uses_absolute_numbers_for_small_bases():
    assert m_.change(7, 3).text == "+4"
    assert m_.change(15, 10).text == "+50%"
    assert m_.change(0.8, 0.9, is_rate=True).text == "-10 p.p."
    assert m_.change(5, None) is None


def test_ratios_and_kpis():
    assert m_.complaint_rate(3, 12) == 0.25 and m_.complaint_rate(1, 0) is None
    assert m_.sla_share(9, 1) == 0.9 and m_.sla_share(0, 0) is None
    kpis = m_.build_kpis({"customer": 20, "related": 12, "complaints": 3, "positive": 2, "answered": 9, "missed": 1})
    assert kpis.neutral == 7 and kpis.requests == 10 and kpis.sla == 0.9


def test_risk_score_components_and_levels():
    calm = m_.risk_score(missed=0, complaints=0, related=20, prev_complaints=0, median_response_s=60, cfg=RISK,
                         sla_minutes=5)
    assert (calm.score, calm.level) == (0, "green")
    bad = m_.risk_score(missed=6, complaints=12, related=20, prev_complaints=4, median_response_s=50 * 60, cfg=RISK,
                        sla_minutes=5)
    assert bad.level == "red" and bad.score == sum(bad.parts.values()) == 100
    assert "6 ta javobsiz" in bad.reasons
    few = m_.risk_score(missed=0, complaints=2, related=2, prev_complaints=0, median_response_s=None, cfg=RISK,
                        sla_minutes=5)
    assert few.parts["rate"] == 0  # under RISK_MIN_RELATED messages the rate is not trusted


def test_overall_status():
    ok = m_.Kpis(related=10, answered=5)
    assert m_.overall_status(ok, ok, "green") == ("green", [])
    level, reasons = m_.overall_status(m_.Kpis(related=10, answered=5, missed=1), None, "green")
    assert level == "yellow" and reasons
    level, _ = m_.overall_status(m_.Kpis(related=30, complaints=10), m_.Kpis(related=30, complaints=4), "green")
    assert level == "red"


# --------------------------------------------------------------------------- text and export


def test_html_and_like_escaping():
    assert esc('<script>alert("x")</script>') == "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"
    assert like_pattern("50%_a\\b") == "%50\\%\\_a\\\\b%"


def test_top_words_drops_stop_words_and_merges_suffixes():
    words = dict(top_words(["Mijoz armatura narxini so'radi", "Mijoz armaturani so'radi", "Сколько стоит арматура"]))
    assert words.get("armatura") == 2 and "mijoz" not in words and "so'radi" not in words


@pytest.mark.parametrize("value", ["=1+1", "+", "-5", "@SUM(A1)", "\tx"])
def test_export_neutralises_formulas(value):
    assert neutralize(value) == "'" + value


def test_csv_and_excel_exports_are_safe():
    frame = pd.DataFrame({"matn": ["+", "=HYPERLINK(\"x\")", "oddiy"], "son": [1, 2, 3]})
    csv_text = to_csv_bytes(frame).decode("utf-8-sig")
    assert "'+" in csv_text and "'=HYPERLINK" in csv_text
    sheet = load_workbook(BytesIO(to_xlsx_bytes(frame))).active
    assert sheet["A2"].value == "'+" and sheet["A3"].data_type == "s"

# Database tests: tests/test_data_layer.py (genesis_test).
