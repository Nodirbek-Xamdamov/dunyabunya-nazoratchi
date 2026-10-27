"""Xodimlar: who answers customers, how fast, in which groups. Every section is a layout block."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import metrics as m_
from core import timeutil as tu
from ui import charts, data, layout, shell, theme
from ui import components as c
from ui.i18n import t
from views import common
from views.common import PageData

PAGE_ID = "staff"


def _answers(d: PageData) -> pd.DataFrame:
    return d.memo("st.answers", lambda: m_.staff_answers(d.period_checks))


def _activity(d: PageData) -> pd.DataFrame:
    period = d.ctx.period
    return d.memo("st.activity", lambda: data.get("staff_activity", period.start_db, period.end_db, live=d.live))


def _no_manager(d: PageData) -> int:
    groups = d.groups
    return int((groups["is_present"] & ~groups["has_manager"]).sum())


def _nothing(d: PageData) -> bool:
    return _answers(d).empty and _activity(d).empty


def _table(d: PageData) -> pd.DataFrame:
    def compute() -> pd.DataFrame:
        answers, activity = _answers(d), _activity(d)
        summary = (answers.groupby("who").agg(
            name=("name", "last"), username=("username", "last"), in_time=("in_time", "sum"), late=("late", "sum"),
            median=("seconds", "median"), answered_groups=("chat_id", "nunique")).reset_index()
            if not answers.empty else pd.DataFrame(columns=["who", "name", "username", "in_time", "late", "median",
                                                            "answered_groups"]))
        staff = activity.rename(columns={"sender_id": "who", "name": "a_name", "username": "a_username"})
        table = staff.merge(summary, on="who", how="outer")
        table["name"] = table["name"].fillna(table.get("a_name")).fillna(table.get("a_username"))
        table.loc[table["who"] == m_.PEERS, "name"] = t("st.peers")
        table["username"] = table["username"].fillna(table.get("a_username"))
        for column in ("in_time", "late", "messages", "groups", "answered_groups"):
            table[column] = table[column].fillna(0).astype(int) if column in table else 0
        table["answers"] = table["in_time"] + table["late"]
        table["share"] = (table["in_time"] / table["answers"].where(table["answers"] > 0) * 100).round(1)
        table["kind"] = [t("st.kind.peer") if who == m_.PEERS else t("st.kind.staff") for who in table["who"]]
        return table.sort_values(["answers", "messages"], ascending=False)

    return d.memo("st.table", compute)


def _kpi_active(d: PageData) -> None:
    c.kpi(t("st.active"), tu.fmt_int(len(_activity(d))))


def _kpi_answers(d: PageData) -> None:
    c.kpi(t("st.answers"), tu.fmt_int(len(_answers(d))))


def _kpi_median(d: PageData) -> None:
    answers = _answers(d)
    c.kpi(t("kpi.median"), tu.fmt_duration(m_.percentile(answers["seconds"], 0.5) if not answers.empty else None))


def _kpi_no_manager(d: PageData) -> None:
    no_manager = _no_manager(d)
    c.kpi(t("st.no_manager", n=no_manager), tu.fmt_int(no_manager))


def _notes(d: PageData) -> None:
    no_manager = _no_manager(d)
    if no_manager:
        st.html(f'<div>{c.chip(t("st.no_manager", n=no_manager), "yellow")}</div>')
        st.caption(t("st.no_manager.note"))
    st.caption(t("st.note"))


def _empty(d: PageData) -> None:
    if _nothing(d):
        c.empty()


_TABLE_COLUMNS = (("st.c.name", "st.c.name"), ("username", "col.username"), ("st.c.kind", "st.c.kind"),
                  ("st.c.in_time", "st.c.in_time"), ("st.c.late", "st.c.late"), ("st.c.share", "st.c.share"),
                  ("st.c.median", "st.c.median"), ("st.c.groups", "st.c.groups"), ("st.c.messages", "st.c.messages"))


def _staff_table(d: PageData) -> None:
    if _nothing(d):
        if d.page_id != PAGE_ID:
            c.empty()
        return
    table = _table(d)
    c.section(t("st.title"))
    view = pd.DataFrame({
        t("st.c.name"): table["name"],
        t("col.username"): table["username"].map(lambda v: f"@{v}" if isinstance(v, str) and v else None),
        t("st.c.kind"): table["kind"],
        t("st.c.in_time"): table["in_time"], t("st.c.late"): table["late"], t("st.c.share"): table["share"],
        t("st.c.median"): table["median"] / 60,
        t("st.c.groups"): table[["groups", "answered_groups"]].max(axis=1),
        t("st.c.messages"): table["messages"],
    })
    st.dataframe(view, hide_index=True, width="stretch", placeholder="—", height=min(36 * len(view) + 40, 560),
                 column_order=layout.shown_columns(d, "st.table", view.columns), column_config={
                     t("st.c.share"): st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%",
                                                                      color=theme.BRAND),
                     t("st.c.median"): st.column_config.NumberColumn(format="%.0f daq")})


def _chart(d: PageData) -> None:
    if _nothing(d):
        if d.page_id != PAGE_ID:
            c.empty()
        return
    top = _table(d)
    top = top[top["answers"] > 0].head(20)
    if top.empty:
        return
    c.section(t("st.chart"))
    c.plot(charts.stacked_hbar(top, "name", [("in_time", theme.POSITIVE, t("st.c.in_time")),
                                            ("late", theme.MISSED, t("st.c.late"))],
                               max(240, 30 * len(top) + 60)), key="st-bars")


B, P = layout.Block, layout.Place
KPI_ACTIVE = B("st.kpi.active", "blk.st.kpi.active", _kpi_active, portable=True, keywords="faol xodimlar soni")
KPI_ANSWERS = B("st.kpi.answers", "blk.st.kpi.answers", _kpi_answers, portable=True, keywords="javoblar soni")
KPI_MEDIAN = B("st.kpi.median", "blk.st.kpi.median", _kpi_median, portable=True, keywords="xodimlar median javob vaqti")
KPI_NO_MANAGER = B("st.kpi.no_manager", "blk.st.kpi.no_manager", _kpi_no_manager, portable=True,
                   keywords="menejersiz guruhlar")
NOTES = B("st.notes", "blk.st.notes", _notes, keywords="izoh, menejer unvoni haqida")
EMPTY = B("st.empty", "blk.st.empty", _empty, hideable=False)
TABLE = B("st.table", "blk.st.table", _staff_table, portable=True, items=_TABLE_COLUMNS,
          hidden_items=("username", "st.c.kind", "st.c.groups", "st.c.messages"),
          keywords="xodimlar jadvali, kim javob beradi")
CHART = B("st.chart", "blk.st.chart", _chart, portable=True, keywords="javoblar soni grafigi, xodimlar grafigi")

_KPI = {"row": "st-kpi", "slots": 3, "gap": "small"}
PAGE = layout.register(layout.Page(PAGE_ID, "page.staff", "staff", (
    P(common.ISSUES, hidden=True),
    P(KPI_ACTIVE, **_KPI), P(KPI_ANSWERS, **_KPI), P(KPI_MEDIAN, **_KPI), P(KPI_NO_MANAGER, hidden=True, **_KPI),
    P(NOTES, hidden=True), P(EMPTY), P(TABLE), P(CHART),
)))


@shell.guard
def render() -> None:
    ctx = shell.header(t("st.title"))
    layout.render_page(PAGE, PageData(ctx))
