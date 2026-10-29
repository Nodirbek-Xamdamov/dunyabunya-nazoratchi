"""«Yangi kamera» → Jonli: the newest annotated frame (zones coloured by state), refreshed every 2 seconds,
and the camera service's health: last heartbeat, camera connection, analysis speed, model time, GPU memory,
the desks right now and the latest events. Only the newest frame exists; nothing is recorded."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from camera import store
from camera import ui as cu
from camera.texts import ct
from core import timeutil as tu
from core.text import esc
from ui import components as c

FRAME_OLD_S = 30
KINDS = {"started": "ishga tushdi", "stopped": "to'xtadi", "connected": "ulandi", "disconnected": "uzildi",
         "error": "xato", "info": "ma'lumot"}


@cu.guard
def render() -> None:
    cu.header("title.live", ct("live.caption"))
    _live()


@st.fragment(run_every=2)
def _live() -> None:
    zone = cu.tz()
    now = cu.now_ts()
    image, meta = store.latest_frame()
    status = store.status()
    left, right = st.columns([3, 1])
    with left:
        if image is None:
            cu.empty(ct("live.no_frame"))
        else:
            st.image(image, width="stretch")
            taken = meta.get("ts")
            if taken and now - float(taken) > FRAME_OLD_S:
                source = cu.source_label(meta.get("source", "live"))
                st.caption(ct("live.old_frame", ago=tu.fmt_duration(now - float(taken)), source=source))
    with right:
        level, text = cu.service_state(status, now)
        st.html(f'<div class="db-fresh">{c.dot(level)}{esc(text)}</div>')
        beat = status.get("heartbeat")
        rows = [
            (ct("live.heartbeat"), datetime.fromtimestamp(float(beat), zone).strftime("%H:%M:%S") if beat else "—"),
            (ct("live.camera"), (ct("live.connected") if status.get("camera_connected") else ct("live.disconnected"))
             if beat else "—"),
            (ct("live.fps"), ct("live.fps.value", fps=status.get("processed_fps", "—")) if beat else "—"),
            (ct("live.infer"), f"{status.get('infer_ms', '—')} ms" if beat else "—"),
            (ct("live.gpu"), f"{status.get('gpu_mem_mb', '—')} MB" if beat else "—"),
            (ct("live.model"), f"{status.get('model', '—')} · {status.get('channel', '—')}" if beat else "—"),
        ]
        cells = "".join(f'<tr><td class="db-muted" style="padding:3px 10px 3px 0">{esc(label)}</td>'
                        f'<td style="padding:3px 0">{esc(value)}</td></tr>' for label, value in rows)
        st.html(f'<table class="db-small" style="width:100%;border-collapse:collapse">{cells}</table>')
        desks = status.get("desks") or []
        if desks and beat and now - float(beat) <= cu.HEARTBEAT_OK_S:
            st.caption(ct("live.desks"))
            for item in desks:
                state = item.get("state")
                if state == "sitting":
                    text, dot = ct("state.sitting"), "green"
                elif state == "hidden":
                    text, dot = ct("state.hidden", minutes=int((item.get("away_s") or 0) // 60)), "yellow"
                else:
                    text, dot = ct("state.away"), "red"
                who = item.get("employee") or item.get("desk")
                st.html(f'<div class="db-fresh">{c.dot(dot)}{esc(who)}: {esc(text)}</div>')
    events = store.events(15)
    if events:
        c.section(ct("live.events"))
        st.dataframe(pd.DataFrame({
            ct("live.col.time"): [datetime.fromtimestamp(e["ts"], zone).strftime("%d.%m %H:%M:%S") for e in events],
            ct("live.col.kind"): [KINDS.get(e["kind"], e["kind"]) for e in events],
            ct("live.col.message"): [e["message"] for e in events],
        }), hide_index=True, width="stretch")
