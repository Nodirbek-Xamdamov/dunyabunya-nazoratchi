"""Kamera (W3-04, D-11, D-31, D-32): for each permanent staff member ("Hodim 1..4", no names) the day's
arrival and departure with their frames, time at work (first entry -> last exit), time in the room (minus
every time out of the room), the exits and a day-long timeline. Data: api.v_staff_day only (read-only);
every number is the camera's estimate ("AI taxmini"). Every section is a layout block."""

from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd
import streamlit as st

from core import camera as cam
from core import timeutil as tu
from core.text import esc
from ui import auth, charts, data, layout, shell
from ui import components as c
from ui.i18n import t
from views.common import PageData

PAGE_ID = "camera"
RECORDING_END = time(22, 0)  # D-24: recording 07:00-22:00; an unseen last exit is drawn up to here (or now)


def _all(d: PageData) -> list[cam.StaffDay]:
    def load() -> list[cam.StaffDay]:
        frame = data.get("staff_days", d.ctx.first_day.isoformat(), d.ctx.last_day.isoformat(), live=d.live)
        return cam.days_from_frame(frame, d.settings.tz)

    return d.memo("cam.all", load)


def _days(d: PageData) -> list[date]:
    return sorted({item.day for item in _all(d)}, reverse=True)


def _day(d: PageData) -> date | None:
    return d.extra.get("day")


def _rows(d: PageData) -> list[cam.StaffDay]:
    day = _day(d)
    return [item for item in _all(d) if item.day == day]


def _mean(values) -> float | None:
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


# --------------------------------------------------------------------------- blocks


def _kpi_present(d: PageData) -> None:
    c.kpi(t("cam.kpi.present"), tu.fmt_int(len(_rows(d))) if _rows(d) else "—")


def _kpi_office(d: PageData) -> None:
    c.kpi(t("cam.kpi.office"), cam.fmt_minutes(_mean(item.office_minutes for item in _rows(d))),
          help=t("cam.office.help"))


def _kpi_room(d: PageData) -> None:
    c.kpi(t("cam.kpi.room"), cam.fmt_minutes(_mean(item.room_minutes for item in _rows(d))), help=t("cam.room.help"))


def _kpi_exits(d: PageData) -> None:
    rows = _rows(d)
    c.kpi(t("cam.kpi.exits"), tu.fmt_int(sum(len(item.exits) for item in rows)) if rows else "—")


def _empty(d: PageData) -> None:
    if not _rows(d):
        c.empty(t("cam.empty"), t("cam.empty.hint"))


def _open_end(d: PageData, day: date) -> datetime:
    end = datetime.combine(day, RECORDING_END, tzinfo=d.settings.tz)
    return min(end, d.ctx.now) if d.ctx.now.date() == day else end


def _timeline(d: PageData) -> None:
    rows, day = _rows(d), _day(d)
    if not rows or day is None:
        return
    c.section(t("cam.timeline"), t("cam.timeline.caption"))
    fig = charts.staff_timeline(rows, _open_end(d, day), d.ctx.now)
    if fig is not None:
        c.plot(fig, key="cam-timeline")


@st.cache_data(max_entries=200, show_spinner=False)
def _frame_bytes(path: str, _mtime: float) -> bytes | None:
    return cam.frame_jpeg(path)


def _frame(d: PageData, stored: str | None, caption: str) -> None:
    if not auth.has("frames"):  # W3-05: camera right and [camera] show_frames
        st.caption(t("cam.frame.restricted"))
        return
    path = cam.frame_file(d.settings.frames_dir, stored)
    picture = _frame_bytes(str(path), path.stat().st_mtime) if path else None
    if picture is None:
        st.caption(t("cam.frame.missing"))
        return
    auth.record("view", "camera_frame", "/".join(cam.frame_tail(stored) or ()))
    st.image(picture, caption=caption, width="stretch")


def _exits_table(item: cam.StaffDay) -> pd.DataFrame:
    return pd.DataFrame({
        t("cam.c.out"): [cam.fmt_time(x.start) for x in item.exits],
        t("cam.c.back"): [cam.fmt_time(x.end) for x in item.exits],
        t("cam.c.minutes"): [cam.fmt_minutes(x.minutes) for x in item.exits],
        t("cam.c.note"): [t("cam.inferred") if x.inferred else "" for x in item.exits],
    })


def _card(d: PageData, item: cam.StaffDay) -> None:
    with st.container(border=True):
        chips = [c.chip(item.ai_label)]
        if item.is_uncertain:
            chips.append(c.chip(t("cam.uncertain"), "yellow"))
        st.html(f'<div><b>{esc(item.name)}</b> &nbsp;{" ".join(chips)}</div>')
        arrive, leave = st.columns(2, gap="small")
        with arrive:
            st.markdown(f"{t('cam.arrived')}: **{cam.fmt_time(item.first_in)}**")
            _frame(d, item.in_frame, t("cam.frame.in", who=item.name, at=cam.fmt_time(item.first_in)))
        with leave:
            st.markdown(f"{t('cam.left')}: **{cam.fmt_time(item.last_out)}**")
            if item.last_out is None:
                st.caption(t("cam.no_exit"))
            else:
                _frame(d, item.out_frame, t("cam.frame.out", who=item.name, at=cam.fmt_time(item.last_out)))
        st.html(f'<div class="db-muted">{esc(t("cam.office"))}: <b>{esc(cam.fmt_minutes(item.office_minutes))}</b>'
                f' · {esc(t("cam.room"))}: <b>{esc(cam.fmt_minutes(item.room_minutes))}</b>'
                f' · {esc(t("cam.away"))}: <b>{esc(cam.fmt_minutes(item.away_minutes))}</b></div>')
        if item.exits:
            table = _exits_table(item)
            st.dataframe(table, hide_index=True, width="stretch", height=36 * len(table) + 40)
        else:
            st.caption(t("cam.no_exits"))
        if item.note:
            st.caption(t("cam.note.view", note=item.note))
        elif item.inferred_frames:
            st.caption(t("cam.note.inferred"))


def _cards(d: PageData) -> None:
    rows = _rows(d)
    if not rows:
        return
    c.section(t("cam.cards"))
    for start in range(0, len(rows), 2):
        for column, item in zip(st.columns(2, gap="medium"), rows[start:start + 2]):
            with column:
                _card(d, item)


def _period_frame(d: PageData) -> pd.DataFrame:
    items = sorted(_all(d), key=lambda item: (item.day, item.staff_no), reverse=True)
    return pd.DataFrame({
        t("cam.c.day"): [tu.fmt_day(item.day) for item in items],
        t("cam.c.who"): [item.name for item in items],
        t("cam.arrived"): [cam.fmt_time(item.first_in) for item in items],
        t("cam.left"): [cam.fmt_time(item.last_out) for item in items],
        t("cam.office"): [cam.fmt_minutes(item.office_minutes) for item in items],
        t("cam.room"): [cam.fmt_minutes(item.room_minutes) for item in items],
        t("cam.away"): [cam.fmt_minutes(item.away_minutes) for item in items],
        t("cam.c.exits"): [len(item.exits) for item in items],
        t("cam.c.state"): [t("cam.uncertain") if item.is_uncertain else item.ai_label for item in items],
    })


def _period_table(d: PageData) -> None:
    if not _all(d):
        return
    table = _period_frame(d)
    c.section(t("cam.period"), t("cam.period.caption"))
    st.dataframe(table, hide_index=True, width="stretch", height=min(36 * len(table) + 40, 560))
    c.download_buttons(table, c.export_name("kamera", d.ctx.first_day, d.ctx.last_day), key="cam-period")


def _notes(d: PageData) -> None:
    st.caption(t("cam.note"))


B, P = layout.Block, layout.Place
KPI_PRESENT = B("cam.kpi.present", "blk.cam.kpi.present", _kpi_present, keywords="kelgan hodimlar soni")
KPI_OFFICE = B("cam.kpi.office", "blk.cam.kpi.office", _kpi_office, keywords="o'rtacha ishda vaqti")
KPI_ROOM = B("cam.kpi.room", "blk.cam.kpi.room", _kpi_room, keywords="o'rtacha xonada vaqti")
KPI_EXITS = B("cam.kpi.exits", "blk.cam.kpi.exits", _kpi_exits, keywords="xonadan chiqishlar soni")
EMPTY = B("cam.empty", "blk.cam.empty", _empty, hideable=False)
TIMELINE = B("cam.timeline", "blk.cam.timeline", _timeline, keywords="kun bo'yi bandlik chizig'i, timeline")
CARDS = B("cam.cards", "blk.cam.cards", _cards, keywords="hodim kartalari, keldi ketdi kadr, chiqishlar")
PERIOD = B("cam.period", "blk.cam.period", _period_table, keywords="davr jadvali, kunlar bo'yicha")
NOTES = B("cam.notes", "blk.cam.notes", _notes, keywords="izoh, hisob qoidasi")

_KPI = {"row": "cam-kpi", "slots": 4, "gap": "small"}
PAGE = layout.register(layout.Page(PAGE_ID, "page.camera", "camera", (
    P(KPI_PRESENT, **_KPI), P(KPI_OFFICE, **_KPI), P(KPI_ROOM, **_KPI), P(KPI_EXITS, **_KPI),
    P(EMPTY), P(TIMELINE), P(CARDS), P(PERIOD), P(NOTES),
), accepts_portable=False))


def _pick_day(d: PageData) -> date | None:
    days = _days(d)
    if not days:
        return None
    if st.session_state.get("cam_day") not in days:
        st.session_state["cam_day"] = days[0]
    return st.selectbox(t("cam.day"), days, key="cam_day", format_func=lambda day: tu.fmt_day(day, with_year=True))


@shell.guard
def render() -> None:
    ctx = shell.header(t("cam.title"))
    if not auth.require("camera", "page", PAGE_ID):
        return
    d = PageData(ctx)
    d.extra["day"] = _pick_day(d)
    layout.render_page(PAGE, d)
