"""Dinamika: week-over-week and month-over-month, weekly categories, 90-day calendar, outlier days.
All windows end at the selected period's last day. Every section is a layout block."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core import metrics as m_
from core import timeutil as tu
from ui import charts, data, layout, shell
from ui import components as c
from ui.i18n import t
from views import common
from views.common import PageData


def _with_rates(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["rate"] = (frame["complaints"] / frame["related"].where(frame["related"] > 0) * 100).round(1)
    total = frame["answered"] + frame["missed"]
    frame["sla"] = (frame["answered"] / total.where(total > 0) * 100).round(1)
    previous = frame["complaints"].shift(1)
    frame["change"] = [m_.change(int(cur), int(prev)).text if pd.notna(prev) else None
                       for cur, prev in zip(frame["complaints"], previous)]
    return frame


def _compare_table(frame: pd.DataFrame) -> None:
    st.dataframe(pd.DataFrame({
        t("tr.c.period"): frame["label"], t("series.related"): frame["related"],
        t("series.complaints"): frame["complaints"], t("tr.c.change"): frame["change"],
        t("kpi.complaint_rate"): frame["rate"], t("series.missed"): frame["missed"], t("kpi.sla"): frame["sla"],
    }).iloc[::-1], hide_index=True, width="stretch", placeholder="—", column_config={
        t("kpi.complaint_rate"): st.column_config.NumberColumn(format="%.1f%%"),
        t("kpi.sla"): st.column_config.NumberColumn(format="%.0f%%")})


def _sum(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    """Totals per week/month; leading periods before monitoring had any data are dropped."""
    totals = frame.groupby(key, sort=True)[["customer", "related", "complaints", "missed", "answered"]].sum().reset_index()
    active = totals.index[totals["customer"] > 0]
    return totals.loc[active[0]:].reset_index(drop=True) if len(active) else totals.tail(1).reset_index(drop=True)


def _anchor(d: PageData) -> date:
    return min(d.ctx.last_day, d.ctx.now.date())


def _weeks_window(d: PageData) -> tu.Period:
    anchor = _anchor(d)
    monday = anchor - timedelta(days=anchor.weekday())
    return d.memo("tr.weeks_window", lambda: tu.day_window(monday - timedelta(weeks=7), anchor, d.settings.tz, "weeks"))


def _weekly(d: PageData) -> pd.DataFrame:
    def compute() -> pd.DataFrame:
        days = common.daily(d.ctx, _weeks_window(d))
        days["week"] = [date.fromisoformat(day) - timedelta(days=date.fromisoformat(day).weekday()) for day in days["day"]]
        weekly = _with_rates(_sum(days, "week"))
        weekly["label"] = [t("tr.week", start=tu.fmt_day(start)) for start in weekly["week"]]
        return weekly

    return d.memo("tr.weekly", compute)


def _long_days(d: PageData) -> pd.DataFrame:
    anchor = _anchor(d)
    first = anchor - timedelta(days=89)
    return d.memo("tr.long", lambda: common.daily(d.ctx, tu.day_window(first, anchor, d.settings.tz, "calendar")))


def _weeks(d: PageData) -> None:
    weekly = _weekly(d)
    c.section(t("tr.weeks"))
    if weekly["related"].sum() == 0:
        c.empty()
        return
    left, right = st.columns([3, 2], gap="large")
    with left:
        c.plot(charts.period_compare(weekly), key="tr-weeks")
    with right:
        _compare_table(weekly)


def _months(d: PageData) -> None:
    anchor = _anchor(d)
    first_month = date(anchor.year, anchor.month, 1)
    for _ in range(5):
        first_month = (first_month - timedelta(days=1)).replace(day=1)
    month_days = common.daily(d.ctx, tu.day_window(first_month, anchor, d.settings.tz, "months"))
    month_days["month"] = month_days["day"].str.slice(0, 7)
    monthly = _with_rates(_sum(month_days, "month"))
    monthly["label"] = [f"{tu.MONTHS[int(key[5:7]) - 1]} {key[:4]}" for key in monthly["month"]]
    c.section(t("tr.months"))
    if monthly["related"].sum() == 0:
        c.empty()
        return
    left, right = st.columns([3, 2], gap="large")
    with left:
        c.plot(charts.period_compare(monthly), key="tr-months")
    with right:
        _compare_table(monthly)


def _by_category(d: PageData) -> None:
    weeks_window = _weeks_window(d)
    c.section(t("tr.by_category"))
    cat = data.get("category_daily", weeks_window.start_db, weeks_window.end_db, weeks_window.offset_modifier(),
                   live=weeks_window.is_open(d.ctx.now))
    if cat.empty or cat["complaints"].sum() == 0:
        c.empty()
        return
    cat["week"] = [t("tr.week", start=tu.fmt_day(date.fromisoformat(day) - timedelta(days=date.fromisoformat(day).weekday())))
                   for day in cat["day"]]
    weekly_cat = cat.groupby(["week", "category"])["complaints"].sum().reset_index()
    c.plot(charts.stacked_categories(weekly_cat, "week", list(_weekly(d)["label"]), "complaints"), key="tr-cat")


def _calendar(d: PageData) -> None:
    anchor = _anchor(d)
    series = _long_days(d).set_index("day")["complaints"]
    c.section(t("tr.calendar"))
    if series.sum() == 0:
        c.empty()
    else:
        c.plot(charts.calendar(series, anchor - timedelta(days=89), anchor), key="tr-calendar")


def _anomalies(d: PageData) -> None:
    long_days = _long_days(d)
    c.section(t("tr.anomalies"), t("tr.anomalies.note"))
    frame = long_days[["day", "complaints", "customer"]].copy()
    started = frame.index[frame["customer"] > 0]
    observed = frame["complaints"].astype("float64")
    if len(started):  # days before monitoring had any data are unknown, not zero
        observed.loc[: started[0] - 1] = float("nan")
    history = observed.shift(1).rolling(14, min_periods=7)
    frame["mean"] = history.mean()
    frame["upper"] = frame["mean"] + 2 * history.std().fillna(0)
    frame["anomaly"] = (frame["complaints"] > frame["upper"]) & (frame["complaints"] >= 3) & frame["mean"].notna()
    shown = frame.tail(60).dropna(subset=["mean"])
    if shown.empty or long_days["complaints"].sum() == 0:
        c.empty()
        return
    c.plot(charts.anomalies(shown), key="tr-anomalies")
    flagged = frame[frame["anomaly"]].sort_values("day", ascending=False)
    if flagged.empty:
        st.caption(t("tr.anomalies.empty"))
    else:
        st.dataframe(pd.DataFrame({
            t("col.date"): [tu.fmt_day(date.fromisoformat(day), True) for day in flagged["day"]],
            t("series.complaints"): flagged["complaints"], t("series.mean"): flagged["mean"].round(1),
        }), hide_index=True, width="stretch")


B, P = layout.Block, layout.Place
WEEKS = B("tr.weeks", "blk.tr.weeks", _weeks, portable=True, keywords="hafta-haftaga, haftalik solishtirish")
MONTHS = B("tr.months", "blk.tr.months", _months, portable=True, keywords="oy-oyga, oylik solishtirish")
BY_CATEGORY = B("tr.by_category", "blk.tr.by_category", _by_category, portable=True,
                keywords="toifalar bo'yicha haftalik shikoyatlar")
CALENDAR = B("tr.calendar", "blk.tr.calendar", _calendar, portable=True, keywords="shikoyatlar kalendari, 90 kun")
ANOMALIES = B("tr.anomalies", "blk.tr.anomalies", _anomalies, portable=True,
              keywords="keskin chetlangan kunlar, anomaliya")
PAGE = layout.register(layout.Page("trends", "page.trends", "trends", (
    P(WEEKS), P(MONTHS), P(BY_CATEGORY), P(CALENDAR, hidden=True), P(ANOMALIES, hidden=True))))


@shell.guard
def render() -> None:
    ctx = shell.header(t("tr.title"))
    st.caption(t("tr.anchor", end=tu.fmt_day(min(ctx.last_day, ctx.now.date()), True)))
    layout.render_page(PAGE, PageData(ctx))
