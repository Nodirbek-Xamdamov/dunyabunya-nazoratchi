"""Javob tezligi: SLA, missed and late answers, answer-time distribution, slowest groups. Every
section is a layout block (ui.layout)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import metrics as m_
from core import timeutil as tu
from ui import charts, layout, nav, shell, theme
from ui import components as c
from ui.i18n import t
from views import common
from views.common import PageData

_VIEWS = ("all", "work", "off")
PAGE_ID = "response"


def _by_work_hours(frame: pd.DataFrame, view: str, settings) -> pd.DataFrame:
    """Dashboard-only view filter by the local hour of the request (WORK_HOURS in dashboard\\.env)."""
    if view == "all" or frame.empty:
        return frame
    local = frame["started_at_ts"].dt.tz_convert(settings.tz)
    minutes = local.dt.hour * 60 + local.dt.minute
    start = settings.work_start.hour * 60 + settings.work_start.minute
    end = settings.work_end.hour * 60 + settings.work_end.minute
    inside = (minutes >= start) & (minutes < end) if start <= end else (minutes >= start) | (minutes < end)
    return frame[inside] if view == "work" else frame[~inside]


def _kpis(frame: pd.DataFrame) -> m_.Kpis:
    counts = frame["status"].value_counts() if not frame.empty else pd.Series(dtype="int64")
    totals = {"answered": int(counts.get(m_.CHECK_ANSWERED, 0)), "missed": int(counts.get(m_.CHECK_MISSED, 0)),
              "waiting": int(counts.get(m_.CHECK_PENDING, 0))}
    return m_.build_kpis(totals, frame)


def _view(d: PageData) -> str:
    """The work-hours filter applies only on this page and only while its control is shown."""
    if d.page_id != PAGE_ID or "rs.filter" not in d.visible_ids:
        return "all"
    return st.session_state.get("rs_view") or "all"


def _frame(d: PageData) -> pd.DataFrame:
    return d.memo("rs.frame", lambda: _by_work_hours(d.period_checks, _view(d), d.settings))


def _cur(d: PageData) -> m_.Kpis:
    return d.memo("rs.cur", lambda: _kpis(_frame(d)))


def _prev(d: PageData) -> m_.Kpis | None:
    previous = d.ctx.previous
    return d.memo("rs.prev", lambda: _kpis(_by_work_hours(common.checks(d.ctx, previous), _view(d), d.settings))
                  if previous else None)


def _frame_or_none(d: PageData) -> pd.DataFrame | None:
    """The period's requests, or None when there are none (this page shows one empty note for all
    sections; on another page the block shows its own)."""
    frame = _frame(d)
    if frame.empty:
        if d.page_id != PAGE_ID:
            c.empty()
        return None
    return frame


def _filter(d: PageData) -> None:
    settings = d.settings
    st.session_state.setdefault("rs_view", "all")
    st.segmented_control(
        t("rs.view"), _VIEWS, key="rs_view", required=True,
        format_func=lambda v: t(f"rs.view.{v}", start=settings.work_start.strftime("%H:%M"),
                                end=settings.work_end.strftime("%H:%M")) if v == "work" else t(f"rs.view.{v}"))
    st.caption(t("rs.view.note"))


def _kpi_sla(d: PageData) -> None:
    cur, prev = _cur(d), _prev(d)
    c.kpi(t("kpi.sla"), tu.fmt_pct(cur.sla), common.change_text(cur.sla, prev.sla, is_rate=True)
          if prev and cur.sla is not None and prev.sla is not None else None, help=t("kpi.sla.help"))


def _kpi_missed(d: PageData) -> None:
    cur, prev = _cur(d), _prev(d)
    c.kpi(t("kpi.missed"), tu.fmt_int(cur.missed), common.change_text(cur.missed, prev.missed if prev else None),
          inverse=True, help=t("kpi.missed.help", late=cur.late))


def _kpi_late(d: PageData) -> None:
    cur, prev = _cur(d), _prev(d)
    c.kpi(t("kpi.late"), tu.fmt_int(cur.late), common.change_text(cur.late, prev.late if prev else None),
          inverse=True, help=t("kpi.late.help"))


def _kpi_never(d: PageData) -> None:
    cur, prev = _cur(d), _prev(d)
    c.kpi(t("kpi.never"), tu.fmt_int(cur.never_answered),
          common.change_text(cur.never_answered, prev.never_answered if prev else None), inverse=True)


def _kpi_median(d: PageData) -> None:
    cur, prev = _cur(d), _prev(d)
    c.kpi(t("kpi.median"), tu.fmt_duration(cur.median_response_s),
          common.duration_change(cur.median_response_s, prev.median_response_s if prev else None), inverse=True,
          help=t("kpi.median.help"))


def _kpi_p90(d: PageData) -> None:
    cur, prev = _cur(d), _prev(d)
    c.kpi(t("kpi.p90"), tu.fmt_duration(cur.p90_response_s),
          common.duration_change(cur.p90_response_s, prev.p90_response_s if prev else None), inverse=True,
          help=t("kpi.p90.help"))


def _requests(d: PageData) -> None:
    st.caption(f"{t('kpi.requests')}: {tu.fmt_int(_cur(d).requests)} · {t('kpi.requests.help')}")


def _empty(d: PageData) -> None:
    if _frame(d).empty:
        c.empty()


def _hist(d: PageData) -> None:
    frame = _frame_or_none(d)
    if frame is None:
        return
    c.section(t("rs.hist"))
    minutes = m_.response_durations(frame) / 60
    if minutes.empty:
        c.empty()
    else:
        c.plot(charts.response_histogram(minutes, d.settings.sla_minutes), key="rs-hist")


def _by_hour(d: PageData) -> None:
    frame = _frame_or_none(d)
    if frame is None:
        return
    c.section(t("rs.by_hour"))
    hours = frame.assign(hour=frame["started_at_ts"].dt.tz_convert(d.settings.tz).dt.hour)
    by_hour = pd.DataFrame({"hour": range(24)}).merge(
        hours.groupby("hour").agg(answered=("status", lambda s: int((s == m_.CHECK_ANSWERED).sum())),
                                  missed=("status", lambda s: int((s == m_.CHECK_MISSED).sum()))).reset_index(),
        on="hour", how="left").fillna(0)
    c.plot(charts.answered_missed(by_hour, "hour", [f"{h:02d}" for h in by_hour["hour"]]), key="rs-hours")


_WORST_COLUMNS = (("kpi.requests", "kpi.requests"), ("kpi.missed", "kpi.missed"), ("kpi.sla", "kpi.sla"),
                  ("kpi.median", "kpi.median"))


def _worst(d: PageData) -> None:
    frame = _frame_or_none(d)
    if frame is None:
        return
    c.section(t("rs.worst"))
    medians = m_.group_median_response(frame)
    per_group = frame.groupby(["chat_id", "title"]).agg(
        answered=("status", lambda s: int((s == m_.CHECK_ANSWERED).sum())),
        missed=("status", lambda s: int((s == m_.CHECK_MISSED).sum()))).reset_index()
    per_group["requests"] = per_group["answered"] + per_group["missed"]
    per_group = per_group[per_group["requests"] >= 3]
    if per_group.empty:
        c.empty()
        return
    per_group["sla"] = (per_group["answered"] / per_group["requests"] * 100).round(1)
    per_group["median"] = per_group["chat_id"].map(medians) / 60
    worst = per_group.sort_values(["sla", "missed"], ascending=[True, False]).head(15).reset_index(drop=True)
    st.caption(t("rs.worst.hint"))
    table = pd.DataFrame({
        t("col.group"): worst["title"], t("kpi.requests"): worst["requests"], t("kpi.missed"): worst["missed"],
        t("kpi.sla"): worst["sla"], t("kpi.median"): worst["median"],
    })
    event = st.dataframe(table, hide_index=True, width="stretch", placeholder="—", key="rs_worst", on_select="rerun",
                         selection_mode="single-row", column_order=layout.shown_columns(d, "rs.worst", table.columns),
                         column_config={
                             t("kpi.sla"): st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%",
                                                                           color=theme.BRAND),
                             t("kpi.median"): st.column_config.NumberColumn(format="%.0f daq")})
    rows = event.selection.rows if event and event.selection else []
    if rows and nav.page("groups") is not None:
        st.switch_page(nav.page("groups"), query_params=common.group_link_params(int(worst.loc[rows[0], "chat_id"])))


def _missed(d: PageData) -> None:
    frame = _frame_or_none(d)
    if frame is None:
        return
    tz = d.settings.tz
    c.section(t("gr.missed_list"))
    missed = frame[frame["status"] == m_.CHECK_MISSED]
    never = missed[missed["later_at"].isna()]
    late = missed[missed["later_at"].notna()]
    tabs = st.tabs([f"{t('rs.never')} ({len(never)})", f"{t('rs.late')} ({len(late)})"])
    base = lambda part: pd.DataFrame({  # noqa: E731
        t("col.time"): common.local_time_column(part, "started_at", tz),
        t("col.group"): part["title"],
        t("col.customer"): part["asker_name"].fillna(part["asker_username"]),
        t("col.request"): part["request_text"],
    })
    with tabs[0]:
        if never.empty:
            c.empty(t("rs.never.empty"), " ")
        else:
            st.dataframe(base(never.sort_values("started_at", ascending=False)), hide_index=True, width="stretch",
                         placeholder="—", column_config={t("col.time"): st.column_config.DatetimeColumn(
                             format="DD.MM.YYYY HH:mm")})
    with tabs[1]:
        if late.empty:
            c.empty(t("rs.late.empty"), " ")
        else:
            part = late.sort_values("started_at", ascending=False)
            table = base(part)
            table[t("col.later")] = part["later_name"].fillna(part["later_username"]).values
            table[t("col.staff")] = part["later_is_staff"].map({1: t("yes"), 0: t("no")}).values
            table[t("col.after")] = (part["later_s"] / 60).round(0).values
            st.dataframe(table, hide_index=True, width="stretch", placeholder="—", column_config={
                t("col.time"): st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
                t("col.after"): st.column_config.NumberColumn(format="%.0f daq")})


# --------------------------------------------------------------------------- layout

B, P = layout.Block, layout.Place
FILTER = B("rs.filter", "blk.rs.filter", _filter, keywords="ish vaqti filtri, barcha vaqt, ish vaqtidan tashqari")
KPI_SLA = B("rs.kpi.sla", "blk.rs.kpi.sla", _kpi_sla, portable=True, keywords="5 daqiqada javob, SLA")
KPI_MISSED = B("rs.kpi.missed", "blk.rs.kpi.missed", _kpi_missed, portable=True, keywords="javobsiz so'rovlar")
KPI_LATE = B("rs.kpi.late", "blk.rs.kpi.late", _kpi_late, portable=True, keywords="kechikib javob berilgan")
KPI_NEVER = B("rs.kpi.never", "blk.rs.kpi.never", _kpi_never, portable=True, keywords="umuman javob yo'q")
KPI_MEDIAN = B("rs.kpi.median", "blk.rs.kpi.median", _kpi_median, portable=True, keywords="median javob vaqti")
KPI_P90 = B("rs.kpi.p90", "blk.rs.kpi.p90", _kpi_p90, portable=True, keywords="P90 javob vaqti")
REQUESTS = B("rs.requests", "blk.rs.requests", _requests, keywords="so'rovlar soni izohi")
EMPTY = B("rs.empty", "blk.rs.empty", _empty, hideable=False)
HIST = B("rs.hist", "blk.rs.hist", _hist, portable=True, keywords="javob vaqti taqsimoti, gistogramma")
BY_HOUR = B("rs.by_hour", "blk.rs.by_hour", _by_hour, portable=True, keywords="so'rov soati bo'yicha, soatlar")
WORST = B("rs.worst", "blk.rs.worst", _worst, portable=True, items=_WORST_COLUMNS,
          keywords="eng yomon guruhlar, sekin javob beradigan guruhlar")
MISSED = B("rs.missed", "blk.rs.missed", _missed, portable=True,
           keywords="javobsiz so'rovlar ro'yxati, kechikib javob berilganlar")

_KPI = {"row": "rs-kpi", "slots": 3, "gap": "small"}
PAGE = layout.register(layout.Page(PAGE_ID, "page.response", "response", (
    P(common.ISSUES, hidden=True), P(FILTER, hidden=True),
    P(KPI_SLA, **_KPI), P(KPI_MISSED, **_KPI), P(KPI_LATE, hidden=True, **_KPI), P(KPI_NEVER, hidden=True, **_KPI),
    P(KPI_MEDIAN, **_KPI), P(KPI_P90, hidden=True, **_KPI),
    P(REQUESTS, hidden=True), P(EMPTY),
    P(HIST, row="rs-charts", hidden=True), P(BY_HOUR, row="rs-charts"),
    P(WORST),
    P(common.WAITING, row="rs-bottom", weight=2), P(MISSED, row="rs-bottom", weight=3),
)))


@shell.guard
def render() -> None:
    ctx = shell.header(t("rs.title"))
    layout.render_page(PAGE, PageData(ctx))
