"""Hisobotlar arxivi: the bot's Excel files (reports\\hisobot_YYYY-MM-DD.xlsx) and the stored numbers and
summaries of a report day. Files are listed only by that exact name pattern; no user-given paths."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core import metrics as m_
from core import timeutil as tu
from core.settings import DASHBOARD_DIR
from core.text import esc
from ui import data, layout, shell
from ui import components as c
from ui.i18n import t
from views.common import PageData

_PATTERN = re.compile(r"^hisobot_(\d{4}-\d{2}-\d{2})\.xlsx$")


@dataclass(frozen=True)
class ReportFile:
    day: date
    path: Path
    size: int
    modified: datetime


def report_files(settings) -> list[ReportFile]:
    folder = DASHBOARD_DIR / "demo" / "reports" if settings.demo else settings.reports_dir
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob("hisobot_*.xlsx"):
        match = _PATTERN.match(path.name)
        if match and path.is_file():
            stat = path.stat()
            found.append(ReportFile(date.fromisoformat(match.group(1)), path, stat.st_size,
                                    datetime.fromtimestamp(stat.st_mtime)))
    return sorted(found, key=lambda item: item.day, reverse=True)


def _files(d: PageData) -> None:
    c.section(t("rp.files"))
    files = report_files(d.settings)
    if not files:
        c.empty(t("rp.files.empty"), " ")
        return
    st.dataframe(pd.DataFrame({
        t("col.date"): [tu.fmt_day(item.day, True) for item in files],
        t("col.file"): [item.path.name for item in files],
        t("col.size"): [round(item.size / 1024, 1) for item in files],
        t("col.modified"): [item.modified for item in files],
    }), hide_index=True, width="stretch", placeholder="—", column_config={
        t("col.size"): st.column_config.NumberColumn(format="%.1f KB"),
        t("col.modified"): st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm")})
    names = [item.path.name for item in files]
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    with left:
        chosen = st.selectbox(t("col.file"), names, key="rp_file")
    with right:
        item = files[names.index(chosen)]
        st.download_button(t("rp.download"), item.path.read_bytes(), file_name=item.path.name,
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/download:", on_click="ignore", width="stretch", key="rp_dl")


def _day(d: PageData) -> None:
    days = data.get("report_dates", live=True)
    c.section(t("rp.day"))
    if not days:
        c.empty(t("rp.none"), " ")
        return
    day = st.selectbox(t("rp.day"), days, key="rp_day", label_visibility="collapsed",
                       format_func=lambda value: tu.fmt_day(date.fromisoformat(value), True))
    left, right = st.columns([2, 3], gap="large")
    with left:
        c.section(t("rp.stats"))
        stats = data.get("daily_stats_for", day, live=False)
        if stats.empty:
            c.empty(t("rp.none"), " ")
        else:
            st.dataframe(pd.DataFrame({
                t("col.category"): stats["category"], t("series.related"): stats["total"],
                t("series.complaints"): stats["complaints"], t("sent.Ijobiy"): stats["positive"],
                t("sent.Neytral"): stats["neutral"], t("kpi.share"): stats["share"],
            }), hide_index=True, width="stretch", placeholder="—",
                column_config={t("kpi.share"): st.column_config.NumberColumn(format="%d%%")})
    with right:
        c.section(t("rp.summaries"))
        summaries = data.get("stored_summaries", date.fromisoformat(day), date.fromisoformat(day), live=False)
        if summaries.empty:
            c.empty(t("rp.none"), " ")
        order = {name: index for index, name in enumerate([m_.SUMMARY_OVERALL, *m_.category_names()])}
        for row in sorted(summaries.itertuples(), key=lambda r: order.get(r.category, 99)):
            label = t("ov.summary.ai") if row.category == m_.SUMMARY_OVERALL else row.category
            with st.expander(c.md_escape(label), expanded=row.category == m_.SUMMARY_OVERALL):
                st.html(f'<div class="db-small">{esc(row.summary_text)}</div>')


B, P = layout.Block, layout.Place
FILES = B("rp.files", "blk.rp.files", _files, keywords="excel hisobotlar, fayllar, yuklab olish")
DAY = B("rp.day", "blk.rp.day", _day, keywords="hisobot kuni, saqlangan raqamlar va xulosalar")
PAGE = layout.register(layout.Page("reports", "page.reports", "reports", (P(FILES), P(DAY))))


@shell.guard
def render() -> None:
    ctx = shell.header(t("rp.title"))
    layout.render_page(PAGE, PageData(ctx))
