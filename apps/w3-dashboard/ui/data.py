"""Cached data access for the pages. Every call goes to core.queries through one short read-only
connection. Results of periods that are still running are cached for CACHE_TTL_OPEN seconds, closed
periods for CACHE_TTL_CLOSED; the cache key is the query name + its arguments (period and filters).

W3-05: rights are applied here, after the cache, for every caller (pages, layout blocks, own pages):
raw message content is blanked for users without the ``raw`` right, camera queries are refused without
the ``camera`` right, and frame paths are blanked without the ``frames`` right."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import db, queries
from core.settings import load_settings
from ui import auth

_SETTINGS = load_settings()
RAW_COLUMNS = ("text", "transcript", "reply_text", "media_path", "request_text")  # message content
CAMERA_QUERIES = ("staff_days",)
FRAME_COLUMNS = ("first_in_frame", "last_out_frame")


def _run(name: str, args: tuple, kwargs: tuple):
    with db.session(load_settings()) as session:
        return getattr(queries, name)(session, *args, **dict(kwargs))


@st.cache_data(ttl=_SETTINGS.ttl_open, max_entries=600, show_spinner=False)
def _cached_open(name: str, args: tuple, kwargs: tuple, source: str):
    return _run(name, args, kwargs)


@st.cache_data(ttl=_SETTINGS.ttl_closed, max_entries=600, show_spinner=False)
def _cached_closed(name: str, args: tuple, kwargs: tuple, source: str):
    return _run(name, args, kwargs)


def _without_frames(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for column in FRAME_COLUMNS:
        if column in frame:
            frame[column] = None
    if "frames" in frame:
        frame["frames"] = [[{**item, "path": None} for item in value] if isinstance(value, list) else value
                           for value in frame["frames"]]
    return frame


def _without_raw(frame: pd.DataFrame) -> pd.DataFrame:
    hidden = [column for column in RAW_COLUMNS if column in frame]
    if not hidden:
        return frame
    frame = frame.copy()
    for column in hidden:
        frame[column] = None
    if "media_status" in frame:
        frame["media_status"] = None  # no picture without the raw right
    return frame


def apply_rights(name: str, result):
    if name in CAMERA_QUERIES:
        if not auth.has("camera"):
            raise auth.AccessDenied(name)
        if isinstance(result, pd.DataFrame) and not auth.has("frames"):
            result = _without_frames(result)
    if isinstance(result, pd.DataFrame) and not auth.has("raw"):
        result = _without_raw(result)
    return result


def get(name: str, *args, live: bool = True, **kwargs):
    """``get("totals", start, end)``; ``live=False`` for periods that are already over."""
    if name in CAMERA_QUERIES and not auth.has("camera"):
        raise auth.AccessDenied(name)  # not even read from the database
    source = load_settings().db.label
    cached = _cached_open if live else _cached_closed
    return apply_rights(name, cached(name, args, tuple(sorted(kwargs.items())), source))


def clear() -> None:
    _cached_open.clear()
    _cached_closed.clear()
