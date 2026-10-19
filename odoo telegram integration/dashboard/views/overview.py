"""Umumiy: "is everything fine?" in three seconds -- status, key numbers, groups that need attention,
then the summary, charts, requests waiting right now and the latest complaints. Every section is a
layout block (ui.layout), so it can be hidden, moved or added to another page from the chat."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import streamlit as st

from core import metrics as m_
from core import timeutil as tu
from core.text import esc
from ui import charts, data, layout, narrative, nav, shell, theme
from ui import components as c
from ui.i18n import t
from views import common
from views.common import PageData


def _more_link(page_key: str, **extra: str) -> None:
    """Small "Batafsil" link under a KPI card to the page that explains the number."""
    page = nav.page(page_key)
    if page is not None:
        st.page_link(page, label=t("ov.more"), icon=":material/arrow_forward:",
                     query_params={**shell.global_params(), **extra})


def _sparks(d: PageData) -> dict:
    def compute() -> dict:
        days = d.days
        medians = common.daily_medians(common.checks(d.ctx, d.window), d.settings.tz).reindex(days["day"])
        answered_missed = days["answered"] + days["missed"]
        return {
            "complaints": common.spark(days["complaints"]), "missed": common.spark(days["missed"]),
            "sla": common.spark((days["answered"] / answered_missed.where(answered_missed > 0)) * 100),
            "related": common.spark(days["related"]), "positive": common.spark(days["positive"]),
            "median": common.spark(medians.values),
        }

    return d.memo("ov.sparks", compute)


def _window_note(d: PageData) -> str:
    return f" · {tu.fmt_period(d.window)}" if d.window is not d.ctx.period else ""


def _young(d: PageData) -> None:
    common.young_data_note(d.ctx)


def _status(d: PageData) -> None:
    level, reasons = m_.overall_status(d.cur, d.prev, common.freshness_level(d.ctx))
    c.status_bar(level, reasons)
    if d.cur.missed and level != "green" and nav.page("response") is not None:
        st.page_link(nav.page("response"), label=t("ov.status.link"), icon=":material/arrow_forward:",
                     query_params=shell.global_params())


# --------------------------------------------------------------------------- KPI cards


def _kpi_complaints(d: PageData) -> None:
    cur, prev = d.cur, d.prev
    c.kpi(t("kpi.complaints"), t("kpi.complaints.value", n=tu.fmt_int(cur.complaints), rate=tu.fmt_pct(cur.complaint_rate)),
          common.change_text(cur.complaints, prev.complaints if prev else None), inverse=True,
          compare=prev is not None, help=t("kpi.complaints.help"), spark=_sparks(d)["complaints"])
    _more_link("categories")


def _kpi_missed(d: PageData) -> None:
    cur, prev = d.cur, d.prev
    c.kpi(t("kpi.missed"), tu.fmt_int(cur.missed), common.change_text(cur.missed, prev.missed if prev else None),
          inverse=True, compare=prev is not None, help=t("kpi.missed.help", late=cur.late), spark=_sparks(d)["missed"])
    _more_link("response")


def _kpi_sla(d: PageData) -> None:
    cur, prev = d.cur, d.prev
    c.kpi(t("kpi.sla"), tu.fmt_pct(cur.sla),
          common.change_text(cur.sla, prev.sla, is_rate=True) if prev and cur.sla is not None and prev.sla is not None else None,
          compare=prev is not None, help=t("kpi.sla.help"), spark=_sparks(d)["sla"])
    _more_link("response")


def _kpi_related(d: PageData) -> None:
    cur, prev = d.cur, d.prev
    c.kpi(t("kpi.related"), tu.fmt_int(cur.related), common.change_text(cur.related, prev.related if prev else None),
          neutral=True, compare=prev is not None, help=t("kpi.related.help", filtered=tu.fmt_int(cur.filtered)),
          spark=_sparks(d)["related"])
    _more_link("categories")


def _kpi_positive(d: PageData) -> None:
    cur, prev = d.cur, d.prev
    names = list(m_.category_names())
    c.kpi(t("kpi.positive"), tu.fmt_int(cur.positive), common.change_text(cur.positive, prev.positive if prev else None),
          compare=prev is not None, help=t("kpi.positive.help"), spark=_sparks(d)["positive"])
    positive = m_.POSITIVE_CATEGORY
    _more_link("categories", **({shell.QP_CATEGORY: str(names.index(positive) + 1)} if positive in names else {}))


def _kpi_median(d: PageData) -> None:
    cur, prev = d.cur, d.prev
    c.kpi(t("kpi.median"), tu.fmt_duration(cur.median_response_s),
          common.duration_change(cur.median_response_s, prev.median_response_s if prev else None), inverse=True,
          compare=prev is not None, help=t("kpi.median.help"), spark=_sparks(d)["median"])
    _more_link("response")


# --------------------------------------------------------------------------- sections


def _attention(d: PageData) -> None:
    groups, settings = d.groups, d.settings
    c.section(t("ov.attention"))
    risky = groups[groups["risk"] >= settings.risk.yellow].sort_values(["risk", "missed", "complaints"], ascending=False)
    if risky.empty:
        c.empty(t("ov.attention.all_green"), t("ov.attention.all_green.hint", yellow=settings.risk.yellow))
        return
    groups_page = nav.page("groups")
    for row in risky.head(5).itertuples():
        left, right = st.columns([6, 1.3], vertical_alignment="center")
        with left:
            chips = "".join(c.chip(reason, row.risk_level) for reason in row.risk_reasons)
            st.html(f'<div class="db-risk-row">{common.risk_badge(row.risk_level, row.risk)}'
                    f'<div><div class="db-risk-title">{esc(row.title)}</div><div>{chips}</div></div></div>')
        with right:
            if groups_page is not None:
                st.page_link(groups_page, label=t("ov.attention.open"), icon=":material/arrow_forward:",
                             query_params=common.group_link_params(int(row.chat_id)))


def _summary(d: PageData) -> None:
    ctx, groups = d.ctx, d.groups
    c.section(t("ov.summary"))
    top = groups.sort_values("complaints", ascending=False).head(1)
    top_group = (str(top.iloc[0]["title"]), int(top.iloc[0]["complaints"])) if not top.empty else None
    left, right = st.columns(2)
    with left:
        st.caption(t("ov.summary.rule"))
        c.summary_list(narrative.summary(d.cur, d.prev, d.cats, top_group))
    with right:
        st.caption(t("ov.summary.ai"))
        first, last = common.report_days(ctx)
        stored = data.get("stored_summaries", first, last, m_.SUMMARY_OVERALL, live=ctx.live)
        if stored.empty:
            st.caption(t("ov.summary.ai_none"))
        for index, row in enumerate(stored.head(1).itertuples()):  # the latest; older ones: Hisobotlar arxivi
            day = pd.to_datetime(row.date).date()
            with st.expander(t("ov.summary.ai_window", window=common.report_window_text(day, ctx)), expanded=index == 0):
                st.html(f'<div class="db-small">{esc(row.summary_text)}</div>')


def _daily(d: PageData) -> None:
    c.section(t("ov.daily"), t("ov.daily.help") + _window_note(d))
    days = d.days
    if days["customer"].sum() == 0:
        c.empty()
    else:
        c.plot(charts.daily_dynamics(days), key="ov-daily")


def _categories(d: PageData) -> None:
    c.section(t("ov.categories"), t("ov.categories.help"))
    cats = d.cats
    if cats["related"].sum() == 0:
        c.empty()
    else:
        c.plot(charts.category_bars(cats), key="ov-cats")


def _sentiment(d: PageData) -> None:
    c.section(t("ov.sentiment"), t("ov.sentiment.help"))
    cur = d.cur
    if cur.related == 0:
        c.empty()
    else:
        c.plot(charts.sentiment_donut(cur.complaints, cur.neutral, cur.positive), key="ov-sent")


def _hours(d: PageData) -> None:
    window = d.window
    c.section(t("ov.hours"), t("ov.hours.help") + _window_note(d))
    hours = data.get("hour_weekday", window.start_db, window.end_db, window.offset_modifier(), None,
                     live=window.is_open(d.ctx.now))
    if hours.empty:
        c.empty()
    else:
        c.plot(charts.heat_hours(hours), key="ov-hours")


def _group_heat(d: PageData) -> None:
    ctx = d.ctx
    c.section(t("ov.group_heat"), t("ov.group_heat.help"))
    heat = data.get("group_category_complaints", ctx.period.start_db, ctx.period.end_db, 15, live=ctx.live)
    if heat.empty:
        c.empty(t("ov.latest.empty"), " ")
        return
    totals = heat.groupby("chat_id")["complaints"].sum().sort_values(ascending=False)
    titles = heat.drop_duplicates("chat_id").set_index("chat_id")["title"].to_dict()
    columns = [name for name in m_.category_names() if name in set(heat["category"])]
    c.plot(charts.heat_matrix(heat, "chat_id", "category", "complaints", list(totals.index), columns,
                              theme.HEAT_RED, max(260, 28 * len(totals) + 80), t("series.complaints"),
                              row_labels=titles), key="ov-gheat")


def _latest(d: PageData) -> None:
    ctx = d.ctx
    c.section(t("ov.latest"))
    frame = data.get("latest_complaints", ctx.period.start_db, ctx.period.end_db, 5, live=ctx.live)
    if frame.empty:
        c.empty(t("ov.latest.empty"), " ")
        return
    for record in common.records(frame):
        row = SimpleNamespace(**record)
        when = tu.fmt_dt(tu.from_db(row.sent_at_utc, ctx.settings.tz))
        label = f"{c.md_escape(row.title)} · {when} · {c.md_escape(row.category or '')}"
        with st.expander(label, icon=":material/report:"):
            st.html(f'<div><b>{esc(row.summary or "")}</b></div>'
                    f'<div class="db-muted" style="margin-top:.3rem">{esc(t("ov.original"))}: '
                    f'{esc(common.message_text(record))}</div>')
            groups_page = nav.page("groups")
            if groups_page is not None:
                st.page_link(groups_page, label=t("se.open_group"), icon=":material/arrow_forward:",
                             query_params=common.group_link_params(int(row.chat_id)))


# --------------------------------------------------------------------------- layout

B = layout.Block
YOUNG = B("ov.young", "blk.ov.young", _young, keywords="monitoring yangi boshlangan, eslatma")
STATUS = B("ov.status", "blk.ov.status", _status, portable=True,
           keywords="hammasi joyidami, holat, status, yashil sariq qizil")
KPI_COMPLAINTS = B("ov.kpi.complaints", "blk.ov.kpi.complaints", _kpi_complaints, portable=True,
                   keywords="shikoyat, shikoyatlar soni, norozilik, жалобы")
KPI_MISSED = B("ov.kpi.missed", "blk.ov.kpi.missed", _kpi_missed, portable=True,
               keywords="javobsiz, javob berilmagan so'rovlar, без ответа")
KPI_SLA = B("ov.kpi.sla", "blk.ov.kpi.sla", _kpi_sla, portable=True,
            keywords="5 daqiqada javob, SLA, javob ulushi, foiz")
ATTENTION = B("ov.attention", "blk.ov.attention", _attention, portable=True,
              keywords="diqqat, xavfli guruhlar, risk, muammoli guruhlar")
KPI_RELATED = B("ov.kpi.related", "blk.ov.kpi.related", _kpi_related, portable=True,
                keywords="kompaniyaga oid xabarlar, xabarlar soni, murojaatlar")
KPI_POSITIVE = B("ov.kpi.positive", "blk.ov.kpi.positive", _kpi_positive, portable=True,
                 keywords="ijobiy fikrlar, maqtov, rahmat, позитив")
KPI_MEDIAN = B("ov.kpi.median", "blk.ov.kpi.median", _kpi_median, portable=True,
               keywords="median javob vaqti, javob tezligi, daqiqa")
SUMMARY = B("ov.summary", "blk.ov.summary", _summary, portable=True,
            keywords="kun xulosasi, AI xulosa, qisqacha, итог")
DAILY = B("ov.daily", "blk.ov.daily", _daily, portable=True,
          keywords="kunlik dinamika, grafik, kunlar bo'yicha")
CATEGORIES = B("ov.categories", "blk.ov.categories", _categories, portable=True,
               keywords="toifalar, bo'limlar grafigi, kategoriya")
SENTIMENT = B("ov.sentiment", "blk.ov.sentiment", _sentiment, portable=True,
              keywords="kayfiyat, shikoyat ijobiy neytral ulushi, doira")
HOURS = B("ov.hours", "blk.ov.hours", _hours, portable=True,
          keywords="soatlar, hafta kunlari, qachon yozishadi, issiqlik xaritasi")
GROUP_HEAT = B("ov.group_heat", "blk.ov.group_heat", _group_heat, portable=True,
               keywords="guruh va toifa bo'yicha shikoyatlar, shikoyat xaritasi")
LATEST = B("ov.latest", "blk.ov.latest", _latest, portable=True,
           keywords="so'nggi shikoyatlar, oxirgi shikoyatlar ro'yxati")

P = layout.Place
_KPI = {"slots": 3, "gap": "small"}
# Simple by default: status, the three key numbers, the categories (bo'limlar), groups that need
# attention, the daily trend, the summary and the latest complaints. Everything marked hidden=True is
# shown when someone asks for it in the chat.
PAGE = layout.register(layout.Page("overview", "page.overview", "overview", (
    P(YOUNG, hidden=True), P(common.ISSUES, hidden=True), P(STATUS),
    P(KPI_COMPLAINTS, row="ov-kpi1", **_KPI), P(KPI_MISSED, row="ov-kpi1", **_KPI), P(KPI_SLA, row="ov-kpi1", **_KPI),
    P(CATEGORIES, row="ov-cats", weight=3), P(SENTIMENT, row="ov-cats", weight=2, hidden=True),
    P(ATTENTION, row="ov-attention", weight=7), P(common.WAITING, row="ov-attention", weight=5),
    P(KPI_RELATED, row="ov-kpi2", hidden=True, **_KPI), P(KPI_POSITIVE, row="ov-kpi2", hidden=True, **_KPI),
    P(KPI_MEDIAN, row="ov-kpi2", hidden=True, **_KPI),
    P(DAILY), P(SUMMARY),
    P(HOURS, row="ov-heat", hidden=True), P(GROUP_HEAT, row="ov-heat", hidden=True),
    P(LATEST),
)))


@shell.guard
def render() -> None:
    ctx = shell.header(t("ov.title"))
    layout.render_page(PAGE, PageData(ctx))
