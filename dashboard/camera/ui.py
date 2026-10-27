"""Streamlit pieces shared by the «Yangi kamera» pages: error guard, header, source picker, the day
table, the hour table and the day timeline."""

from __future__ import annotations

import functools
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta, tzinfo

import pandas as pd
import plotly.express as px
import streamlit as st

from camera import report, store
from camera.texts import ct
from core.settings import load_settings
from ui import components as c
from ui import theme

log = logging.getLogger("dashboard.camera")

SEG_COLORS = {"seg.sitting": theme.POSITIVE, "seg.break": theme.NEUTRAL, "seg.prayer": "#A855F7",
              "seg.no_data": theme.COMPLAINT}
HEARTBEAT_OK_S = 60


def guard(render):
    """Friendly messages instead of stack traces, as ui.shell.guard does for the monitoring pages."""

    @functools.wraps(render)
    def wrapper():
        try:
            render()
        except store.CameraDbMissing as exc:
            st.error(ct("err.no_db", path=str(exc)))
        except (sqlite3.Error, OSError, ValueError, KeyError) as exc:
            log.warning("camera page %s: %s", render.__name__, type(exc).__name__)
            st.error(ct("err.db"))

    return wrapper


def tz() -> tzinfo:
    return load_settings().tz


def header(title_key: str, caption: str | None = None) -> None:
    st.title(ct(title_key), anchor=False)
    st.caption(caption or ct("note.desk_time"))


def source_label(source: str) -> str:
    return ct("source.live") if source == "live" else ct("source.clip", name=source.split(":", 1)[-1])


def source_picker(key: str) -> str:
    """Live camera, or a test clip that was run through the pipeline (?manba=clip:<file> preselects it)."""
    found = store.sources()
    if len(found) == 1:
        return "live"
    wanted = st.query_params.get("manba")
    if wanted in found and key not in st.session_state:
        st.session_state[key] = wanted
    return st.selectbox(ct("source"), found, format_func=source_label, key=key)


def clip_days(source: str, zone) -> list[date]:
    """Days a test clip covers (so the date picker can jump there)."""
    frame = store.intervals(0, 4102444800, source)
    if frame.empty:
        return []
    return sorted({datetime.fromtimestamp(ts, zone).date() for ts in frame["start_ts"]})


def service_state(status: dict, now: float) -> tuple[str, str]:
    beat = status.get("heartbeat")
    if not beat:
        return "red", ct("svc.never")
    if now - float(beat) <= HEARTBEAT_OK_S:
        return "green", ct("svc.ok")
    return "red", ct("svc.stale")


def desk_states(status: dict, now: float) -> dict[int, str]:
    """employee id -> text of the current state, from the service's last status (if it is alive)."""
    alive = status.get("heartbeat") and now - float(status["heartbeat"]) <= HEARTBEAT_OK_S
    result: dict[int, str] = {}
    desks = {d["id"]: d for d in store.desks(active_only=False)}
    for item in status.get("desks") or []:
        desk = desks.get(item.get("desk_id"))
        if not desk or desk.get("employee_id") is None:
            continue
        if not alive:
            text = ct("state.unknown")
        elif item.get("state") == "sitting":
            text = ct("state.sitting")
        elif item.get("state") == "hidden":
            text = ct("state.hidden", minutes=int((item.get("away_s") or 0) // 60))
        else:
            text = ct("state.away")
        result[desk["employee_id"]] = text
    return result


def day_table(rows: list[report.EmployeeDay], zone: tzinfo, states: dict[int, str] | None,
              with_required: bool = True) -> pd.DataFrame:
    """with_required=False for a test clip: it covers only part of a day, so "required" makes no sense."""
    data = []
    for row in rows:
        item = {
            ct("col.employee"): row.employee,
            ct("col.desk"): row.desk or "—",
            ct("col.first"): report.clock(row.first, zone),
            ct("col.last"): report.clock(row.last, zone),
            ct("col.sat"): report.hhmm(row.sat_s),
            ct("col.prayer"): report.hhmm(row.prayer_s) if row.prayer_s else "—",
            ct("col.work"): report.hhmm(row.work_s),
            ct("col.required"): report.hhmm(row.required_s) if row.required_s else "—",
            ct("col.diff"): report.hhmm(row.diff_s, signed=True) if row.required_s else "—",
            ct("col.breaks"): row.break_count,
            ct("col.longest"): report.hhmm(row.longest_s),
            ct("col.no_data"): report.hhmm(row.no_data_s) if row.no_data_s >= 60 else "—",
        }
        if not with_required:
            del item[ct("col.required")], item[ct("col.diff")]
        if states is not None:
            item[ct("col.state")] = states.get(row.employee_id, ct("state.off"))
        data.append(item)
    return pd.DataFrame(data)


def hours_table(rows: list[report.EmployeeDay], first_hour: int, last_hour: int) -> tuple[pd.DataFrame, dict]:
    hours = list(range(first_hour, last_hour + 1))
    data = []
    for row in rows:
        item = {ct("col.employee"): row.employee}
        for hour in hours:
            item[f"{hour:02d}"] = int(round(row.hours.get(hour, 0)))
        item[ct("col.sat")] = report.hhmm(row.sat_s)
        data.append(item)
    config = {f"{hour:02d}": st.column_config.ProgressColumn(f"{hour:02d}", min_value=0, max_value=60, format="%d")
              for hour in hours}
    return pd.DataFrame(data), config


def timeline(rows: list[report.EmployeeDay], gaps: list[tuple[float, float]], day: date, zone: tzinfo,
             window: tuple[float, float] | None, now: float):
    items = []

    def add(name: str, start: float, end: float, kind: str) -> None:
        items.append({"who": name, "start": datetime.fromtimestamp(start, zone), "end": datetime.fromtimestamp(end, zone),
                      "kind": ct(kind)})

    for row in rows:
        for s, e in row.segments:
            add(row.employee, s, e, "seg.sitting")
        for s, e in row.breaks:
            add(row.employee, s, e, "seg.break")
        for s, e, _name in row.prayers:
            add(row.employee, s, e, "seg.prayer")
        for s, e in gaps:
            add(row.employee, s, e, "seg.no_data")
    if not items:
        return None
    frame = pd.DataFrame(items)
    order = [row.employee for row in rows]
    fig = px.timeline(frame, x_start="start", x_end="end", y="who", color="kind",
                      color_discrete_map={ct(k): v for k, v in SEG_COLORS.items()},
                      category_orders={"who": order, "kind": [ct(k) for k in SEG_COLORS]})
    day_start, _day_end = report.day_bounds(day, zone)
    starts = [x.timestamp() for x in frame["start"]] + ([window[0]] if window else [])
    ends = [x.timestamp() for x in frame["end"]] + ([window[1]] if window else [])
    left = max(day_start, min(starts) - 1800)
    right = min(day_start + 86400, max(ends) + 1800)
    if window:
        fig.add_vrect(x0=datetime.fromtimestamp(window[0], zone), x1=datetime.fromtimestamp(window[1], zone),
                      fillcolor=theme.BRAND, opacity=0.06, line_width=0, layer="below")
    if day_start <= now <= day_start + 86400:
        fig.add_vline(x=datetime.fromtimestamp(now, zone), line_dash="dot", line_color=theme.NEUTRAL)
    fig.update_yaxes(title=None, categoryorder="array", categoryarray=list(reversed(order)))  # first row on top
    fig.update_xaxes(title=None, range=[datetime.fromtimestamp(left, zone), datetime.fromtimestamp(right, zone)],
                     tickformat="%H:%M")
    fig.update_traces(hovertemplate="%{y}<br>%{base|%H:%M} – %{x|%H:%M}<extra></extra>")
    fig.update_layout(template=theme.TEMPLATE_NAME, height=110 + 46 * len(order), barmode="overlay",
                      legend_title_text="", bargap=0.35)
    return fig


def intervals_table(frame: pd.DataFrame, zone: tzinfo) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    reasons = {"away": ct("reason.away"), "no_data": ct("reason.no_data"), "stopped": ct("reason.stopped")}
    return pd.DataFrame({
        ct("col.date"): [datetime.fromtimestamp(s, zone).strftime("%Y-%m-%d") for s in frame["start_ts"]],
        ct("col.employee"): frame["employee"].fillna("—"),
        ct("col.desk"): frame["desk"],
        ct("col.start"): [datetime.fromtimestamp(s, zone).strftime("%H:%M:%S") for s in frame["start_ts"]],
        ct("col.end"): [datetime.fromtimestamp(e, zone).strftime("%H:%M:%S") for e in frame["end_ts"]],
        ct("col.duration"): [report.hhmm(e - s) for s, e in zip(frame["start_ts"], frame["end_ts"])],
        ct("col.reason"): [ct("reason.open") if is_open else reasons.get(r, r or "—")
                           for r, is_open in zip(frame["close_reason"], frame["open"])],
    })


def now_ts() -> float:
    return time.time()


def days_back(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def employees_for_report() -> list[dict]:
    return store.employees(active_only=False)


def empty(text: str) -> None:
    c.empty(text, " ")
