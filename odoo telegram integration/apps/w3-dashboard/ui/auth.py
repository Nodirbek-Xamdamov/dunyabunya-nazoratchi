"""Login and rights (W3-05). Users and roles live in ``core.dashboard_users`` (core.users); the signed-in
user is kept in this browser session only (a page reload asks again). Passwords are never put in the URL,
the session state or the logs. Failed attempts are limited per client: after 5 failures the client waits
(60 s, doubling, max 15 min). Every sign-in, sign-out, refusal and every view of camera data or raw
messages goes to the access log (core.access_log)."""

from __future__ import annotations

import threading
import time

import streamlit as st

from core import access_log, db, users
from core.settings import Settings, load_settings
from core.text import esc
from ui.i18n import t

_LOCAL = ("127.0.0.1", "localhost", "::1", "")
_FREE_ATTEMPTS = 5
STATE_KEY = "auth_user"
# navigation pages that need a right beyond signing in
PAGE_RIGHTS = {"camera": "camera", "search": "raw", "system": "system"}


class AccessDenied(db.DataError):
    """The signed-in user has no right to this data."""


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
        address = st.context.ip_address
    except Exception:  # older/unknown runtime
        return "local"
    return address if isinstance(address, str) and address else "local"


def current() -> users.Access | None:
    return users.Access.from_state(st.session_state.get(STATE_KEY))


def has(right: str) -> bool:
    """``raw`` | ``camera`` | ``frames`` | ``system``; False when nobody is signed in."""
    access = current()
    if access is None:
        return False
    if right == "frames":
        return access.frames(load_settings().show_frames)
    return bool(getattr(access, right, False))


def page_allowed(page_key: str) -> bool:
    right = PAGE_RIGHTS.get(page_key)
    return right is None or has(right)


def record(action: str, object_type: str | None = None, object_id: str | None = None,
           details: dict | None = None, actor: str | None = None) -> None:
    access = current()
    access_log.write(load_settings().access_log, actor or (access.login if access else "-"), action, object_type,
                     object_id, details, _client())


def require(right: str, object_type: str, object_id: str | None = None) -> bool:
    """True when allowed (the view is logged); otherwise a short notice, a logged refusal and False."""
    if has(right):
        record("view", object_type, object_id)
        return True
    record("denied", object_type, object_id, {"right": right})
    st.info(t(f"auth.denied.{right}"), icon=":material/lock:")
    return False


def refuse_unsafe_host(settings: Settings) -> None:
    """D-15: the dashboard listens on this machine only."""
    address = str(st.get_option("server.address") or "")
    if address not in _LOCAL or not settings.is_local_host:
        st.error(t("auth.host_not_local", host=esc(address or settings.host)))
        st.stop()


def _authenticate(settings: Settings, login: str, password: str) -> tuple[users.Access | None, bool]:
    """(access, login exists); the second value only decides whether the log may name the login."""
    with db.session(settings) as session:
        access = users.authenticate(session, login, password)
        return access, access is not None or users.known_login(session, login)


def _no_users(settings: Settings) -> bool:
    try:
        with db.session(settings) as session:
            return users.count_active(session) == 0
    except db.DataError:
        return False  # the login form shows the database problem


def gate(settings: Settings) -> None:
    refuse_unsafe_host(settings)
    if current() is not None:
        return
    limiter, client = _limiter(), _client()
    _left, middle, _right = st.columns([1, 1.2, 1])
    with middle:
        st.subheader(t("auth.title"), anchor=False)
        if _no_users(settings):
            st.warning(t("auth.no_users"), icon=":material/person_add:")
            st.code(t("auth.no_users.cmd"), language="powershell")
            st.stop()
        st.caption(t("auth.caption"))
        with st.form("login", border=True):
            login = st.text_input(t("auth.login"), max_chars=40)
            password = st.text_input(t("auth.password"), type="password", max_chars=200)
            submitted = st.form_submit_button(t("auth.submit"), type="primary", width="stretch")
        if submitted:
            wait = limiter.wait_seconds(client)
            if wait:
                st.error(t("auth.locked", seconds=wait))
                st.stop()
            try:
                access, known = _authenticate(settings, login, password)
            except db.DataError:
                st.error(t("auth.db_error"))
                st.stop()
            if access is not None:
                limiter.reset(client)
                st.session_state[STATE_KEY] = access.to_state()
                record("login", "dashboard", details={"role": access.role, "camera": access.camera})
                st.rerun()
            limiter.fail(client)
            record("login_failed", "dashboard", actor=users.normalize_login(login) if known else "?")
            st.error(t("auth.wrong"))
    st.stop()


def sidebar_box() -> None:
    access = current()
    if access is None:
        return
    rights = [t(f"auth.role.{access.role}")] + ([t("auth.right.camera")] if access.camera else [])
    st.caption(t("auth.signed_in", login=access.login, rights=", ".join(rights)))
    if st.button(t("auth.logout"), icon=":material/logout:", width="stretch", key="auth_logout"):
        record("logout", "dashboard")
        st.session_state.pop(STATE_KEY, None)
        st.rerun()
