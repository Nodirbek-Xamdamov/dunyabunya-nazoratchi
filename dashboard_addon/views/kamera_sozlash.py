"""«Yangi kamera» → Sozlash: desk zones drawn with the mouse on the newest camera picture, who sits at each
desk, employees and their working hours, the counting rules and the prayer times. The camera service
re-reads all of it within about 10 seconds."""

from __future__ import annotations

import io
from datetime import datetime, time, timedelta

import pandas as pd
import streamlit as st
from PIL import Image

from camera import prayer, store, zone_editor
from camera import ui as cu
from camera.texts import ct
from core import timeutil as tu

DAY_COLS = ("Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya")


@cu.guard
def render() -> None:
    cu.header("title.setup")
    desks_tab, people_tab, settings_tab, prayer_tab = st.tabs(
        [ct("setup.tab.desks"), ct("setup.tab.people"), ct("setup.tab.settings"), ct("setup.tab.prayer")])
    with desks_tab:
        _desks()
    with people_tab:
        _people()
    with settings_tab:
        _settings()
    with prayer_tab:
        _prayer()


# --------------------------------------------------------------------------- desks and zones


def _desks() -> None:
    zone = cu.tz()
    image, meta = store.latest_frame(raw=True)
    if image is None:
        st.warning(ct("setup.frame.none"), icon=":material/warning:")
        return
    width, height = meta.get("width"), meta.get("height")
    if not width or not height:
        width, height = Image.open(io.BytesIO(image)).size
    taken = meta.get("ts")
    when = datetime.fromtimestamp(float(taken), zone).strftime("%d.%m.%Y %H:%M:%S") if taken else "—"
    st.caption(ct("setup.frame", when=when, source=cu.source_label(meta.get("source", "live"))))

    desks = store.desks()
    employees = store.employees(active_only=True)
    names = {e["id"]: e["name"] for e in employees}
    options = [d["id"] for d in desks] + ["new"]
    labels = {d["id"]: f"{d['name']}" + (f" – {d['employee']}" if d.get("employee") else "") for d in desks}
    labels["new"] = ct("setup.desk.new")
    if "cam_desk_next" in st.session_state:  # set by Save / Delete in the previous run
        st.session_state["cam_desk_pick"] = st.session_state.pop("cam_desk_next")
    if st.session_state.get("cam_desk_pick") not in options:
        st.session_state["cam_desk_pick"] = options[0]
    choice = st.segmented_control(ct("setup.desk"), options, format_func=lambda key: labels[key],
                                  key="cam_desk_pick") or options[0]
    desk = next((d for d in desks if d["id"] == choice), None)
    draft_key = f"cam_zone_{choice}"
    if draft_key not in st.session_state:
        st.session_state[draft_key] = [list(p) for p in desk["zone"]] if desk else []

    left, right = st.columns([3, 1])
    with left:
        st.caption(ct("setup.help"))
        zones = [{"id": d["id"], "label": labels[d["id"]], "zone": d["zone"]} for d in desks]
        draft = zone_editor.editor(image, int(width), int(height), zones, choice, draft_key)
    with right:
        name = st.text_input(ct("setup.desk.name"), value=desk["name"] if desk else "", key=f"cam_desk_name_{choice}")
        people = [None] + list(names)
        current = desk.get("employee_id") if desk else None
        employee = st.selectbox(ct("setup.desk.employee"), people,
                                index=people.index(current) if current in people else 0,
                                format_func=lambda key: ct("setup.desk.nobody") if key is None else names[key],
                                key=f"cam_desk_emp_{choice}")
        st.caption(ct("setup.points", count=len(draft)))
        if st.button(ct("setup.undo"), icon=":material/undo:", width="stretch", disabled=not draft,
                     key=f"cam_undo_{choice}"):
            st.session_state[draft_key] = draft[:-1]
            _redraw(draft_key)
        if st.button(ct("setup.clear"), icon=":material/delete_sweep:", width="stretch", disabled=not draft,
                     key=f"cam_clear_{choice}"):
            st.session_state[draft_key] = []
            _redraw(draft_key)
        if desk and st.button(ct("setup.reset"), icon=":material/restart_alt:", width="stretch",
                              key=f"cam_reset_{choice}"):
            st.session_state[draft_key] = [list(p) for p in desk["zone"]]
            _redraw(draft_key)
        if st.button(ct("setup.save"), icon=":material/save:", type="primary", width="stretch", key=f"cam_save_{choice}"):
            if not name.strip():
                st.error(ct("setup.need_name"))
            elif len(draft) < 3:
                st.error(ct("setup.need3"))
            else:
                saved = store.save_desk(desk["id"] if desk else None, name.strip(), employee, draft)
                st.session_state.pop(draft_key, None)
                st.session_state.pop(f"cam_zone_{saved}", None)
                st.session_state["cam_desk_next"] = saved
                st.toast(ct("setup.saved"), icon=":material/check_circle:")
                st.rerun()
        if desk:
            with st.popover(ct("setup.delete"), icon=":material/delete:", width="stretch"):
                if st.button(ct("setup.delete.confirm"), type="primary", key=f"cam_delete_{choice}"):
                    store.delete_desk(desk["id"])
                    st.session_state.pop(draft_key, None)
                    st.session_state["cam_desk_next"] = "new"
                    st.toast(ct("setup.deleted"))
                    st.rerun()


def _redraw(draft_key: str) -> None:
    counter = f"{draft_key}-n"
    st.session_state[counter] = st.session_state.get(counter, 0) + 1  # fresh chart: no stale selection
    st.rerun()


# --------------------------------------------------------------------------- employees


def _hhmm_to_time(value: str) -> time:
    hours, minutes = (int(x) for x in value.split(":"))
    return time(hours, minutes)


def _people() -> None:
    st.caption(ct("setup.people.help"))
    employees = store.employees()
    rows = []
    for e in employees:
        days = {int(x) for x in str(e["workdays"]).split(",") if x.strip().isdigit()}
        row = {"id": e["id"], "name": e["name"], "start": _hhmm_to_time(e["work_start"]),
               "end": _hhmm_to_time(e["work_end"]), "hours": round(e["required_minutes"] / 60, 2),
               "active": bool(e["active"])}
        row.update({col: (index + 1) in days for index, col in enumerate(DAY_COLS)})
        rows.append(row)
    frame = pd.DataFrame(rows, columns=["id", "name", "start", "end", "hours", *DAY_COLS, "active"])
    version = st.session_state.get("cam_people_n", 0)  # a new editor after saving, so it shows the saved rows
    edited = st.data_editor(
        frame, num_rows="add", hide_index=True, width="stretch", key=f"cam_people_{version}",
        column_config={
            "id": None,
            "name": st.column_config.TextColumn(ct("setup.people.name"), required=True, max_chars=60),
            "start": st.column_config.TimeColumn(ct("setup.people.start"), format="HH:mm", step=timedelta(minutes=5),
                                                 default=time(9, 0), required=True),
            "end": st.column_config.TimeColumn(ct("setup.people.end"), format="HH:mm", step=timedelta(minutes=5),
                                               default=time(19, 0), required=True),
            "hours": st.column_config.NumberColumn(ct("setup.people.required"), min_value=0.0, max_value=16.0,
                                                   step=0.5, default=10.0, format="%.1f"),
            **{col: st.column_config.CheckboxColumn(col, default=col != "Ya") for col in DAY_COLS},
            "active": st.column_config.CheckboxColumn(ct("setup.people.active"), default=True),
        })
    if st.button(ct("setup.save"), icon=":material/save:", type="primary", key="cam_people_save"):
        saved = 0
        for row in edited.to_dict("records"):
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            start, end = row.get("start"), row.get("end")
            if not isinstance(start, time) or not isinstance(end, time) or end <= start:
                st.error(ct("setup.people.bad_time", name=name))
                return
            workdays = ",".join(str(i + 1) for i, col in enumerate(DAY_COLS) if row.get(col))
            employee_id = None if pd.isna(row.get("id")) else int(row["id"])
            store.save_employee(employee_id, name, start.strftime("%H:%M"), end.strftime("%H:%M"),
                                int(round(float(row.get("hours") or 0) * 60)), workdays, bool(row.get("active", True)))
            saved += 1
        st.toast(ct("setup.saved"), icon=":material/check_circle:")
        st.session_state["cam_people_n"] = version + 1
        st.rerun()


# --------------------------------------------------------------------------- settings


def _settings() -> None:
    s = store.settings()
    with st.form("cam_settings_form", border=False):
        st.subheader(ct("setup.s.analysis"), anchor=False)
        a, b = st.columns(2)
        with a:
            fps = st.slider(ct("setup.s.target_fps"), 1.0, 10.0, float(s.get("target_fps", 5)), 0.5)
            channel = st.radio(ct("setup.s.channel"), [101, 102], index=0 if int(s.get("channel", 101)) == 101 else 1,
                               format_func=lambda v: ct(f"setup.s.channel.{v}"))
        with b:
            conf = st.slider(ct("setup.s.desk_conf"), 0.2, 0.8, float(s.get("desk_conf", 0.35)), 0.05,
                             help=ct("setup.s.desk_conf.help"))
            anchor = st.radio(ct("setup.s.anchor"), ["center", "upper"], index=0 if s.get("anchor") != "upper" else 1,
                              format_func=lambda v: ct(f"setup.s.anchor.{v}"))
        st.subheader(ct("setup.s.logic"), anchor=False)
        a, b = st.columns(2)
        with a:
            window = st.slider(ct("setup.s.sit_window"), 5, 60, int(s.get("sit_window_s", 10)))
            ratio = st.slider(ct("setup.s.sit_ratio"), 50, 95, int(round(float(s.get("sit_ratio", 0.7)) * 100)), 5)
        with b:
            grace = st.slider(ct("setup.s.away_grace"), 1, 30, int(round(float(s.get("away_grace_s", 600)) / 60)),
                              help=ct("setup.s.away_grace.help"))
            gap = st.slider(ct("setup.s.gap"), 30, 600, int(s.get("gap_s", 60)), 10)
        st.subheader(ct("setup.s.prayer"), anchor=False)
        credit = st.toggle(ct("setup.s.prayer_credit"), value=bool(s.get("prayer_credit", True)))
        a, b, d = st.columns(3)
        with a:
            p_window = st.number_input(ct("setup.s.prayer_window"), 5, 120, int(s.get("prayer_window_min", 30)),
                                       help=ct("setup.s.prayer_window.help"))
        with b:
            p_max = st.number_input(ct("setup.s.prayer_max"), 5, 60, int(s.get("prayer_max_min", 20)))
        with d:
            p_offset = st.number_input(ct("setup.s.prayer_offset"), -60, 60, int(s.get("prayer_offset_min", 0)))
        if st.form_submit_button(ct("setup.s.save"), icon=":material/save:", type="primary"):
            store.save_settings({"target_fps": fps, "channel": channel, "desk_conf": conf, "anchor": anchor,
                                 "sit_window_s": float(window), "sit_ratio": ratio / 100, "away_grace_s": grace * 60.0,
                                 "gap_s": float(gap), "prayer_credit": credit, "prayer_window_min": int(p_window),
                                 "prayer_max_min": int(p_max), "prayer_offset_min": int(p_offset)})
            st.toast(ct("setup.saved"), icon=":material/check_circle:")
    st.caption(f"{ct('live.model')}: {s.get('model')} · imgsz {s.get('imgsz')}")


# --------------------------------------------------------------------------- prayer times


def _prayer() -> None:
    zone = cu.tz()
    st.caption(ct("setup.prayer.caption"))
    offset = float(store.settings().get("prayer_offset_min", 0))
    today = datetime.now(zone).date()
    rows = []
    for i in range(7):
        day = today + timedelta(days=i)
        times = prayer.times_for(day, zone, offset)
        row = {ct("col.date"): tu.fmt_day(day)}
        row.update({ct(f"prayer.{name}"): times[name].strftime("%H:%M") for name in prayer.NAMES})
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
