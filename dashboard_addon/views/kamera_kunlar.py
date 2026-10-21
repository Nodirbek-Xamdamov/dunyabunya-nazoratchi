"""«Yangi kamera» → Kunlar: the period chosen in the sidebar (last 7 / 30 days, a month, any range): totals per
employee, a day-by-employee table of work time and the Excel file (daily summary + intervals)."""

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
    settings = store.settings()
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
    daily: list[dict] = []
    for day in cu.days_back(first, last):
        day_start, day_end = report.day_bounds(day, zone)
        if day_start > now:
            break
        part = intervals[(intervals["start_ts"] < day_end) & (intervals["end_ts"] > day_start)]
        cov_part = cov[(cov["minute_ts"] >= day_start) & (cov["minute_ts"] < day_end)]
        # a day the camera system did not run at all (before it was installed, server off) is not counted
        has_data = len(cov_part) > 0 or len(part) > 0
        rows, _gaps, _share = report.day_report(employees, part, cov_part, day, zone, settings, now, clip=clip)
        for row in rows:
            daily.append({"day": day, "row": row, "data": has_data})
    if not any(item["data"] for item in daily):
        cu.empty(ct("days.empty"))
        return

    c.section(ct("days.summary"), ct("days.summary.caption"))
    summary = []
    names = list(dict.fromkeys(item["row"].employee for item in daily))  # employees in their own order
    for employee in names:
        items = [item["row"] for item in daily if item["row"].employee == employee and item["data"]]
        workdays = [r for r in items if r.required_s > 0]
        line = {ct("col.employee"): employee}
        if not clip:
            line.update({
                ct("col.workdays"): len(workdays),
                ct("col.avg_work"): report.hhmm(sum(r.work_s for r in workdays) / len(workdays)) if workdays else "—",
                ct("col.days_ok"): f"{sum(1 for r in workdays if r.work_s >= r.required_s)} / {len(workdays)}",
            })
        line.update({
            ct("col.total_work"): report.hhmm(sum(r.work_s for r in items)),
            ct("col.prayer"): report.hhmm(sum(r.prayer_s for r in items)),
            ct("col.breaks"): sum(r.break_count for r in items),
            ct("col.no_data"): report.hhmm(sum(r.no_data_s for r in items)),
        })
        summary.append(line)
    st.dataframe(pd.DataFrame(summary), hide_index=True, width="stretch")

    c.section(ct("days.matrix"))
    matrix = {}
    for item in daily:
        label = item["day"].strftime("%d.%m")
        matrix.setdefault(item["row"].employee, {})[label] = report.hhmm(item["row"].work_s) if item["data"] else "—"
    st.dataframe(pd.DataFrame.from_dict(matrix, orient="index").rename_axis(ct("col.employee")).reset_index(),
                 hide_index=True, width="stretch")

    sheet = []
    for item in daily:
        if not item["data"]:
            continue
        row = item["row"]
        sheet.append({
            ct("col.date"): item["day"].isoformat(), ct("col.employee"): row.employee, ct("col.desk"): row.desk,
            ct("col.first"): report.clock(row.first, zone), ct("col.last"): report.clock(row.last, zone),
            ct("col.sat"): report.hhmm(row.sat_s), ct("col.prayer"): report.hhmm(row.prayer_s),
            ct("col.work"): report.hhmm(row.work_s),
            ct("col.required"): report.hhmm(row.required_s) if row.required_s else "—",
            ct("col.diff"): report.hhmm(row.diff_s, signed=True) if row.required_s else "—",
            ct("col.breaks"): row.break_count, ct("col.longest"): report.hhmm(row.longest_s),
            ct("col.no_data"): report.hhmm(row.no_data_s),
        })
    name = f"kamera_{first.isoformat()}_{last.isoformat()}.xlsx"
    st.download_button(ct("export"), workbook({ct("export.sheet.summary"): pd.DataFrame(sheet),
                                               ct("export.sheet.intervals"): cu.intervals_table(intervals, zone)}),
                       file_name=name, icon=":material/table_view:", on_click="ignore", key="cam-days-xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption(f"{datetime.fromtimestamp(now, zone):%H:%M} · {cu.source_label(source)}")
