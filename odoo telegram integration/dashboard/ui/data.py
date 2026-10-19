"""Cached data access for the pages. Every call goes to core.queries through one short read-only
connection. Results of periods that are still running are cached for CACHE_TTL_OPEN seconds, closed
periods for CACHE_TTL_CLOSED; the cache key is the query name + its arguments (period and filters)."""

from __future__ import annotations

import streamlit as st

from core import db, queries
from core.settings import load_settings

_SETTINGS = load_settings()


def _run(name: str, args: tuple, kwargs: tuple):
    with db.session(load_settings()) as session:
        return getattr(queries, name)(session, *args, **dict(kwargs))


@st.cache_data(ttl=_SETTINGS.ttl_open, max_entries=600, show_spinner=False)
def _cached_open(name: str, args: tuple, kwargs: tuple, source: str):
    return _run(name, args, kwargs)


@st.cache_data(ttl=_SETTINGS.ttl_closed, max_entries=600, show_spinner=False)
def _cached_closed(name: str, args: tuple, kwargs: tuple, source: str):
    return _run(name, args, kwargs)


def get(name: str, *args, live: bool = True, **kwargs):
    """``get("totals", start, end)``; ``live=False`` for periods that are already over."""
    source = str(load_settings().db_path)
    cached = _cached_open if live else _cached_closed
    return cached(name, args, tuple(sorted(kwargs.items())), source)


def clear() -> None:
    _cached_open.clear()
    _cached_closed.clear()
