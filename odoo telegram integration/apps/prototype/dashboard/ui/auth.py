"""Password gate (DASHBOARD_PASSWORD). The password is never put in the URL or the logs.
Failed attempts are limited per client: after 5 failures the client waits (60 s, doubling, max 15 min)."""

from __future__ import annotations

import hmac
import threading
import time

import streamlit as st

from core.settings import Settings
from core.text import esc
from ui.i18n import t

_LOCAL = ("127.0.0.1", "localhost", "::1", "")
_FREE_ATTEMPTS = 5


class _Limiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, tuple[int, float]] = {}

    def wait_seconds(self, client: str) -> int:
        with self._lock:
            _failures, until = self._state.get(client, (0, 0.0))
            return max(0, int(until - time.monotonic()))

    def fail(self, client: str) -> None:
        with self._lock:
            failures, _until = self._state.get(client, (0, 0.0))
            failures += 1
            wait = 0.0 if failures < _FREE_ATTEMPTS else min(900.0, 60.0 * 2 ** (failures - _FREE_ATTEMPTS))
            self._state[client] = (failures, time.monotonic() + wait)

    def reset(self, client: str) -> None:
        with self._lock:
            self._state.pop(client, None)


@st.cache_resource
def _limiter() -> _Limiter:
    return _Limiter()


def _client() -> str:
    try:
        return st.context.ip_address or "local"
    except Exception:  # older/unknown runtime
        return "local"


def refuse_unsafe_host(settings: Settings) -> None:
    """A dashboard reachable from other machines must have a password."""
    address = str(st.get_option("server.address") or "")
    exposed = address not in _LOCAL or not settings.is_local_host
    if exposed and not settings.password:
        st.error(t("auth.host_without_password", host=esc(address or settings.host)))
        st.stop()


def gate(settings: Settings) -> None:
    refuse_unsafe_host(settings)
    if not settings.password or st.session_state.get("auth_ok"):
        return
    limiter, client = _limiter(), _client()
    _left, middle, _right = st.columns([1, 1.2, 1])
    with middle:
        st.subheader(t("auth.title"), anchor=False)
        st.caption(t("auth.caption"))
        with st.form("login", border=True):
            password = st.text_input(t("auth.password"), type="password")
            submitted = st.form_submit_button(t("auth.submit"), type="primary", width="stretch")
        if submitted:
            wait = limiter.wait_seconds(client)
            if wait:
                st.error(t("auth.locked", seconds=wait))
            elif hmac.compare_digest(password.encode("utf-8"), settings.password.encode("utf-8")):
                limiter.reset(client)
                st.session_state["auth_ok"] = True
                st.rerun()
            else:
                limiter.fail(client)
                st.error(t("auth.wrong"))
    st.stop()
