"""«Yangi kamera» → Jonli: the newest camera frame, clean (no boxes, zones or names), refreshed every second,
and one line saying whether the camera service is running. Only the newest frame exists; nothing is recorded.
Since 2026-10-22 (owner: "faqat live") the service details, desk states and events are not shown."""

from __future__ import annotations

import streamlit as st

from camera import store
from camera import ui as cu
from camera.texts import ct
from core import timeutil as tu
from core.text import esc
from ui import components as c

FRAME_OLD_S = 30


@cu.guard
def render() -> None:
    cu.header("title.live", ct("live.caption"))
    _live()


@st.fragment(run_every=1)
def _live() -> None:
    now = cu.now_ts()
    image, meta = store.latest_frame(raw=True)
    level, text = cu.service_state(store.status(), now)
    st.html(f'<div class="db-fresh">{c.dot(level)}{esc(text)}</div>')
    if image is None:
        cu.empty(ct("live.no_frame"))
        return
    st.image(image, width="stretch")
    taken = meta.get("ts")
    if taken and now - float(taken) > FRAME_OLD_S:
        source = cu.source_label(meta.get("source", "live"))
        st.caption(ct("live.old_frame", ago=tu.fmt_duration(now - float(taken)), source=source))
