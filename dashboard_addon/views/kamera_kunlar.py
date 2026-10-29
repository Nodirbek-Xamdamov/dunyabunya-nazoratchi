"""«Yangi kamera» → Kunlar: the period chosen in the sidebar (last 7 / 30 days, a month, any range): for every day and
employee when they came, when they left, how long they were outside and how many times, plus the Excel file
(the same table + every outing)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from camera import report, store
from camera import ui as cu
from camera.excel import workbook
from camera.texts import ct
from core import timeutil as tu
from ui import components as c
from ui import shell


@cu.guard
def render() -> None:
    zone = cu.tz()
    ctx = shell.current()
    cu.header("title.days", f"{tu.fmt_period(ctx.period)} · {ct('days.caption')}")
    employees = store.employees()
    source = cu.source_picker("cam_days_source")
    now = cu.now_ts()
    first, last = ctx.first_day, ctx.last_day
    start_ts, _ = report.day_bounds(first, zone)
    _, end_ts = report.day_bounds(last, zone)
    intervals = store.intervals(start_ts, end_ts, source)
    cov = store.coverage(start_ts, end_ts, source)
    if intervals.empty:
        cu.empty(ct("days.empty"))
        return

    clip = source != "live"
    table, outings = [], []
    for day in cu.days_back(first, last):
        day_start, day_end = report.day_bounds(day, zone)
        if day_start > now:
            break
        part = intervals[(intervals["start_ts"] < day_end) & (intervals["end_ts"] > day_start)]
        cov_part = cov[(cov["minute_ts"] >= day_start) & (cov["minute_ts"] < day_end)]
        if len(cov_part) == 0 and len(part) == 0:  # the camera system did not run that day at all
            continue
        rows, _gaps, _share = report.day_report(employees, part, cov_part, day, zone, now, clip=clip)
        frame = cu.day_table(rows, zone, None)
        frame.insert(0, ct("col.date"), day.strftime("%d.%m.%Y"))
        table.append(frame)
        day_outings = cu.outings_table(rows, zone)
        if not day_outings.empty:
            day_outings.insert(0, ct("col.date"), day.strftime("%d.%m.%Y"))
            outings.append(day_outings)
    if not table:
        cu.empty(ct("days.empty"))
        return

    c.section(ct("days.table"), ct("days.table.caption"))
    table_frame = pd.concat(table, ignore_index=True)
    st.dataframe(table_frame, hide_index=True, width="stretch")
    outings_frame = pd.concat(outings, ignore_index=True) if outings else pd.DataFrame()
    name = f"kamera_{first.isoformat()}_{last.isoformat()}.xlsx"
    st.download_button(ct("export"), workbook({ct("export.sheet.summary"): table_frame,
                                               ct("export.sheet.outings"): outings_frame}),
                       file_name=name, icon=":material/table_view:", on_click="ignore", key="cam-days-xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption(f"{datetime.fromtimestamp(now, zone):%H:%M} · {cu.source_label(source)}")
