"""«Yangi kamera» → Bugun: one day per employee - when they came, when they left, how long they were outside and how
many times they went out - then the outings of one chosen employee with the 5-second video of every arrival and
departure. Any day can be picked; test clips can be chosen as the source while the system is being checked."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from camera import report, store
from camera import ui as cu
from camera.excel import workbook
from camera.texts import ct
from ui import components as c


@cu.guard
def render() -> None:
    zone = cu.tz()
    cu.header("title.today")
    employees = store.employees()
    if not any(len(d["zone"]) >= 3 for d in store.desks()):
        st.info(ct("today.no_desks"), icon=":material/info:")
    now = cu.now_ts()
    today = datetime.fromtimestamp(now, zone).date()
    left, middle, _rest = st.columns([2, 1, 2], vertical_alignment="bottom")
    with left:
        source = cu.source_picker("cam_today_source")
    clip = source != "live"
    default = today
    if clip:
        days = cu.clip_days(source, zone)
        default = days[0] if days else today
    with middle:
        day = st.date_input(ct("day"), value=default, max_value=today, format="DD.MM.YYYY", key=f"cam_day_{source}")
    start_ts, end_ts = report.day_bounds(day, zone)
    intervals = store.intervals(start_ts, end_ts, source)
    cov = store.coverage(start_ts, end_ts, source)
    rows, gaps, _share = report.day_report(employees, intervals, cov, day, zone, now, clip=clip)
    live_today = not clip and day == today
    status = store.status() if live_today else {}
    states = cu.desk_states(status, now) if live_today else None

    cols = st.columns(3)
    with cols[0]:
        if states is not None:
            present = sum(1 for text in states.values() if text == ct("state.sitting"))
            c.kpi(ct("kpi.present"), f"{present} / {len(rows)}")
        else:
            c.kpi(ct("kpi.came"), f"{sum(1 for r in rows if r.first is not None)} / {len(rows)}")
    with cols[1]:
        c.kpi(ct("kpi.outside_total"), report.hhmm(sum(r.outside_s for r in rows)))
    with cols[2]:
        if live_today:
            _level, text = cu.service_state(status, now)
            c.kpi(ct("kpi.service"), text)
        else:
            c.kpi(ct("source"), ct("source.clip_short") if clip else ct("source.live"),
                  help=cu.source_label(source) if clip else None)

    c.section(ct("sec.table"))
    if not rows:
        cu.empty(ct("today.empty"))
        return
    summary = cu.day_table(rows, zone, states)
    st.dataframe(summary, hide_index=True, width="stretch")
    if gaps:
        items = ", ".join(f"{report.clock(s, zone)}–{report.clock(e, zone)}" for s, e in gaps)
        st.warning(ct("quality.gaps", items=items), icon=":material/warning:")

    c.section(ct("sec.person"), ct("sec.person.caption"))
    names = {r.employee_id: r.employee for r in rows}
    chosen = st.selectbox(ct("col.employee"), list(names), format_func=names.get, key=f"cam_person_{source}")
    row = next(r for r in rows if r.employee_id == chosen)
    outings = cu.outings_table([row], zone, with_employee=False)
    if outings.empty:
        st.caption(ct("person.no_outings"))
    else:
        st.dataframe(outings, hide_index=True, width="stretch")
    cu.show_clips(row, store.clips(start_ts, end_ts, source), zone)

    summary.insert(0, ct("col.date"), day.isoformat())
    st.download_button(ct("export"), workbook({ct("export.sheet.summary"): summary,
                                               ct("export.sheet.outings"): cu.outings_table(rows, zone)}),
                       file_name=f"kamera_{day.isoformat()}.xlsx", icon=":material/table_view:",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       on_click="ignore", key="cam-today-xlsx")
