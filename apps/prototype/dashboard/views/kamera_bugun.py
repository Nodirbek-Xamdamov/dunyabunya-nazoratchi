"""«Yangi kamera» → Bugun: one day per employee (arrival, last seen, time at the desk, prayer time added to the
work time, breaks, longest sitting, current state), the day timeline, minutes per hour, data quality and the
intervals. Any day can be picked; test clips can be chosen as the source while the system is being checked."""

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
    settings = store.settings()
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
    rows, gaps, share = report.day_report(employees, intervals, cov, day, zone, settings, now, clip=clip)
    live_today = not clip and day == today
    status = store.status() if live_today else {}
    states = cu.desk_states(status, now) if live_today else None
    window = report.work_window(employees, day, zone) if not clip else (
        (float(cov["minute_ts"].min()), float(cov["minute_ts"].max()) + 60) if len(cov) else None)

    cols = st.columns(4)
    with cols[0]:
        if states is not None:
            present = sum(1 for text in states.values() if text == ct("state.sitting"))
            c.kpi(ct("kpi.present"), f"{present} / {len(rows)}")
        else:
            c.kpi(ct("col.employee"), str(len(rows)))
    with cols[1]:
        c.kpi(ct("kpi.sat_total"), report.hhmm(sum(r.sat_s for r in rows)))
    with cols[2]:
        c.kpi(ct("kpi.coverage"), "—" if share is None else f"{share * 100:.0f}%".replace(".", ","),
              help=ct("kpi.coverage.help"))
    with cols[3]:
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
    st.dataframe(cu.day_table(rows, zone, states, with_required=not clip), hide_index=True, width="stretch")

    c.section(ct("sec.timeline"), ct("sec.timeline.caption"))
    fig = cu.timeline(rows, gaps, day, zone, window, now)
    if fig is None:
        cu.empty(ct("today.empty"))
    else:
        c.plot(fig, key="cam-timeline")

    c.section(ct("sec.hours"), ct("sec.hours.caption"))
    first_hour = datetime.fromtimestamp(window[0], zone).hour if window else 8
    last_hour = (datetime.fromtimestamp(window[1] - 1, zone).hour if window else 19)
    seen_hours = [h for r in rows for h in r.hours]
    if seen_hours:
        first_hour, last_hour = min(first_hour, min(seen_hours)), max(last_hour, max(seen_hours))
    table, config = cu.hours_table(rows, first_hour, last_hour)
    st.dataframe(table, hide_index=True, width="stretch", column_config=config)

    c.section(ct("sec.quality"))
    if gaps:
        items = ", ".join(f"{report.clock(s, zone)}–{report.clock(e, zone)}" for s, e in gaps)
        st.warning(ct("quality.gaps", items=items), icon=":material/warning:")
    elif window:
        st.success(ct("quality.ok"), icon=":material/check_circle:")
    if live_today:
        st.caption(ct("quality.future", time=report.clock(now, zone)))

    with st.expander(ct("sec.intervals")):
        frame = cu.intervals_table(intervals, zone)
        if frame.empty:
            st.caption(ct("today.empty"))
        else:
            st.dataframe(frame, hide_index=True, width="stretch")
        summary = cu.day_table(rows, zone, None, with_required=not clip)
        summary.insert(0, ct("col.date"), day.isoformat())
        st.download_button(ct("export"), workbook({ct("export.sheet.summary"): summary,
                                                   ct("export.sheet.intervals"): frame}),
                           file_name=f"kamera_{day.isoformat()}.xlsx", icon=":material/table_view:",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           on_click="ignore", key="cam-today-xlsx")
