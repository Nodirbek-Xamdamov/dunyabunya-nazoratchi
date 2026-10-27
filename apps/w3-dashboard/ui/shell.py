"""Page shell: global period filter in the sidebar, URL query parameters, shared context and the
error guard every page runs under."""

from __future__ import annotations

import functools
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import streamlit as st

from core import db
from core import metrics as m_
from core import timeutil as tu
from core.settings import Settings, load_settings
from core.text import esc
from ui import auth
from ui import components as c
from ui import overrides
from ui import data
from ui.i18n import t

log = logging.getLogger("dashboard.ui")

QP_PRESET, QP_FROM, QP_TO, QP_COMPARE = "p", "from", "to", "cmp"
QP_GROUP, QP_CATEGORY = "g", "c"
_GLOBAL_KEYS = (QP_PRESET, QP_FROM, QP_TO, QP_COMPARE)
MIN_DAY = date(2025, 1, 1)  # earliest selectable day (monitoring started 2026-10-13)


@dataclass(frozen=True)
class Ctx:
    settings: Settings
    now: datetime
    period: tu.Period
    previous: tu.Period | None  # None when comparison is off

    @property
    def live(self) -> bool:
        return self.period.is_open(self.now)

    @property
    def first_day(self) -> date:
        return self.period.start.date()

    @property
    def last_day(self) -> date:
        return (self.period.end - timedelta(microseconds=1)).date()


def _parse_day(value) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _init_from_url(now: datetime) -> None:
    if st.session_state.get("_url_loaded"):
        return
    params = st.query_params
    preset = params.get(QP_PRESET, tu.DEFAULT_PRESET)
    st.session_state["f_preset"] = preset if preset in tu.PRESETS else tu.DEFAULT_PRESET
    first, last = _parse_day(params.get(QP_FROM)), _parse_day(params.get(QP_TO))
    today = now.date()
    if not first or not last:
        first, last = today - timedelta(days=6), today
    clamp = lambda day: min(max(day, MIN_DAY), today)  # noqa: E731
    first, last = clamp(first), clamp(last)
    st.session_state["f_range"] = (min(first, last), max(first, last))
    st.session_state["f_compare"] = params.get(QP_COMPARE, "1") != "0"
    st.session_state["_url_loaded"] = True


def global_params() -> dict[str, str]:
    """Current global filters as URL query parameters (used for links between pages)."""
    preset = st.session_state.get("f_preset", tu.DEFAULT_PRESET)
    values = {QP_PRESET: preset}
    if preset == "custom":
        chosen = st.session_state.get("f_range") or ()
        if isinstance(chosen, (tuple, list)) and chosen:
            values[QP_FROM], values[QP_TO] = chosen[0].isoformat(), chosen[-1].isoformat()
    if not st.session_state.get("f_compare", True):
        values[QP_COMPARE] = "0"
    return values


def set_param(key: str, value: str | None) -> None:
    if value is None:
        if key in st.query_params:
            del st.query_params[key]
    elif st.query_params.get(key) != value:
        st.query_params[key] = value


def _sync_url() -> None:
    values = global_params()
    for key in _GLOBAL_KEYS:
        set_param(key, values.get(key))


def _freshness(settings: Settings, now: datetime) -> None:
    try:
        info = data.get("freshness")
    except db.DataError:
        return
    last = tu.from_db(info.get("last_message"), settings.tz)
    if last is None:
        st.caption(t("side.no_data"))
        return
    age = (now - last).total_seconds() / 60
    level = m_.freshness_level(age, settings)
    st.html(f'<div class="db-fresh">{c.dot(level)}{esc(t("side.last_data", ago=tu.fmt_ago(last, now)))}</div>')


_AUTO_SECONDS = 300


@st.fragment(run_every=_AUTO_SECONDS)
def _auto_refresh() -> None:
    """While the toggle is on, re-runs the whole page every 5 minutes (live data caches expire after
    CACHE_TTL_OPEN). A normal page run renders this fragment right after recording its own start time,
    so only the timer-driven runs trigger the refresh."""
    if time.monotonic() - st.session_state.get("_full_run_at", 0.0) >= _AUTO_SECONDS - 5:
        st.rerun(scope="app")


def sidebar(settings: Settings) -> Ctx:
    now = tu.now_local(settings.tz)
    _init_from_url(now)
    with st.sidebar:
        preset = st.selectbox(t("side.period"), tu.PRESETS, format_func=lambda key: t(f"preset.{key}"),
                              key="f_preset")
        custom = None
        if preset == "custom":
            value = st.date_input(t("side.range"), value=None, key="f_range", min_value=MIN_DAY,
                                  max_value=now.date(), format="DD.MM.YYYY")
            if isinstance(value, (tuple, list)) and value:
                custom = (value[0], value[-1])
        st.toggle(t("side.compare"), key="f_compare")
        if st.button(t("side.refresh"), icon=":material/refresh:", width="stretch"):
            data.clear()
            st.rerun()
        st.toggle(t("side.auto"), key="f_auto")
        st.session_state["_full_run_at"] = time.monotonic()
        if st.session_state.get("f_auto"):
            _auto_refresh()
        _freshness(settings, now)
        if settings.demo:
            st.badge(t("side.source.demo"), icon=":material/science:", color="orange")
        auth.sidebar_box()
    period = tu.resolve_period(preset, now, settings.report_time, custom)
    previous = period.previous(now) if st.session_state.get("f_compare", True) else None
    _sync_url()
    ctx = Ctx(settings, now, period, previous)
    st.session_state["_ctx"] = ctx
    return ctx


def current() -> Ctx:
    ctx = st.session_state.get("_ctx")
    if ctx is None:  # a page rendered without the app shell (tests)
        ctx = sidebar(load_settings())
    return ctx


def header(title: str) -> Ctx:
    """Page title, period line, demo banner and the definitions popover."""
    ctx = current()
    if ctx.settings.demo:
        c.demo_banner()
    left, right = st.columns([5, 1], vertical_alignment="bottom")
    with left:
        st.title(overrides.page_title(title), anchor=False)
        period_text = tu.fmt_period(ctx.period)
        if ctx.previous is not None:
            period_text += f" · {t('compare.short')}"
        st.caption(period_text, help=t("compare.help", previous=tu.fmt_period(ctx.previous)) if ctx.previous else None)
    with right:
        with st.popover(t("defs.button"), icon=":material/help:", width="stretch"):
            risk = ctx.settings.risk
            st.markdown(t("defs.body", red=risk.red, yellow=risk.yellow, red_minus=risk.red - 1))
    return ctx


def guard(render):
    """Runs a page and turns data problems into friendly messages (no stack traces)."""

    @functools.wraps(render)
    def wrapper():
        try:
            render()
        except auth.AccessDenied as exc:  # a block asked for data this user may not see
            auth.record("denied", "query", str(exc))
            st.info(t("auth.denied.camera" if str(exc) in data.CAMERA_QUERIES else "auth.denied.raw"),
                    icon=":material/lock:")
        except db.DatabaseMissing as exc:
            if load_settings().demo:
                st.error(t("error.missing_demo"))
            else:
                st.error(t("error.missing_db", name=esc(str(exc))))
        except db.QueryTimeout:
            st.error(t("error.timeout"))
        except (db.DataError, sqlite3.Error) as exc:
            log.warning("data error on page %s: %s", render.__name__, type(exc).__name__)
            st.error(t("error.data"))

    return wrapper
