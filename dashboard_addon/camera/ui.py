"""Streamlit pieces shared by the «Yangi kamera» pages: error guard, header, source picker, the day table
(keldi, ketdi, tashqarida), the outings of one employee and their 5-second videos."""

from __future__ import annotations

import functools
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta, tzinfo

import pandas as pd
import streamlit as st

from camera import report, store
from camera.texts import ct
from core.settings import load_settings
from ui import components as c

log = logging.getLogger("dashboard.camera")

HEARTBEAT_OK_S = 60
CLIP_MATCH_S = 2.0  # a video belongs to an arrival/departure when its time is this close
VIDEO_COLUMNS = 3


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


def left_text(row: report.EmployeeDay, zone: tzinfo) -> str:
    if row.first is None:
        return "—"
    return ct("still_in") if row.still_in else report.clock(row.last, zone)


def day_table(rows: list[report.EmployeeDay], zone: tzinfo, states: dict[int, str] | None) -> pd.DataFrame:
    data = []
    for row in rows:
        item = {
            ct("col.employee"): row.employee,
            ct("col.first"): report.clock(row.first, zone),
            ct("col.last"): left_text(row, zone),
            ct("col.outside"): report.hhmm(row.outside_s) if row.first is not None else "—",
            ct("col.out_count"): len(row.outings),
        }
        if states is not None:
            item[ct("col.state")] = states.get(row.employee_id, ct("state.off"))
        data.append(item)
    return pd.DataFrame(data)


def outings_table(rows: list[report.EmployeeDay], zone: tzinfo, with_employee: bool = True) -> pd.DataFrame:
    data = []
    for row in rows:
        for number, outing in enumerate(row.outings, start=1):
            item = {ct("col.employee"): row.employee} if with_employee else {}
            item.update({
                ct("col.number"): number,
                ct("col.left"): report.clock(outing.left, zone, seconds=True),
                ct("col.back"): report.clock(outing.back, zone, seconds=True),
                ct("col.outside"): report.hhmm(outing.outside_s),
                ct("col.note"): ct("note.no_data", time=report.hhmm(outing.no_data_s)) if outing.no_data_s >= 60 else "",
            })
            data.append(item)
    return pd.DataFrame(data)


def moments(row: report.EmployeeDay) -> list[tuple[str, str, float]]:
    """(label key, video kind, time) of the day in order: came, every outing (left, back), went home."""
    if row.first is None:
        return []
    items = [("moment.first", "in", row.first)]
    for outing in row.outings:
        items += [("moment.left", "out", outing.left), ("moment.back", "in", outing.back)]
    if not row.still_in:
        items.append(("moment.last", "out", row.last))
    return items


def find_clip(clips: pd.DataFrame, employee_id: int, kind: str, ts: float) -> str | None:
    if clips.empty:
        return None
    near = clips[(clips["employee_id"] == employee_id) & (clips["kind"] == kind) & ((clips["event_ts"] - ts).abs()
                                                                                     <= CLIP_MATCH_S)]
    return None if near.empty else str(near.iloc[0]["path"])


def show_clips(row: report.EmployeeDay, clips: pd.DataFrame, zone: tzinfo) -> None:
    items = moments(row)
    if not items:
        st.caption(ct("today.empty"))
        return
    for start in range(0, len(items), VIDEO_COLUMNS):
        cols = st.columns(VIDEO_COLUMNS)
        for col, (label, kind, ts) in zip(cols, items[start:start + VIDEO_COLUMNS]):
            with col:
                st.markdown(f"**{ct(label)}** · {report.clock(ts, zone, seconds=True)}")
                relative = find_clip(clips, row.employee_id, kind, ts)
                path = store.clip_file(relative) if relative else None
                if path is None:
                    st.caption(ct("clip.none"))
                elif path.suffix == ".jpg":
                    st.image(path.read_bytes(), width="stretch")
                else:
                    st.video(path.read_bytes(), format="video/mp4")


def now_ts() -> float:
    return time.time()


def days_back(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def employees_for_report() -> list[dict]:
    return store.employees(active_only=False)


def empty(text: str) -> None:
    c.empty(text, " ")
