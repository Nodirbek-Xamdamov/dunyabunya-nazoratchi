"""Applies the design chosen in the chat (core/theme.py) on every run: Streamlit's theme goes into
config.toml, an open page reloads itself once after the design changed (Streamlit gives a new theme
only to pages loaded after that), and the extra CSS is added."""

from __future__ import annotations

import logging

import streamlit as st

from core import theme
from core.settings import Settings
from ui import custom

log = logging.getLogger("dashboard.design")

SEEN = "_design_seen"  # session state: the design this page shows (or RELOAD_PENDING)
RELOAD_PENDING = "reload"
RELOAD_DELAY_MS = 1500  # Streamlit re-reads config.toml within about a second
REOPEN_CHAT = "chat"  # query parameter: ui.chat opens the panel again after the reload

_RELOAD = """<script>
setTimeout(function () {
  var url = new URL(window.location.href);
  %s
  window.location.replace(url.toString());
}, %d);
</script>"""


def apply(settings: Settings, overlay: dict) -> None:
    tokens = overlay.get("theme") or {}
    st.session_state[custom.SESSION_DESIGN] = tokens
    fingerprint = theme.fingerprint(tokens)
    try:
        written = theme.sync_config(settings.theme_config_path, tokens)
    except OSError as exc:
        log.warning("theme not written to %s: %s", settings.theme_config_path.name, type(exc).__name__)
        written = False
    if written or st.session_state.get(SEEN, fingerprint) != fingerprint:
        # Until the page has reloaded (a new session), every run asks again: writing config.toml makes
        # Streamlit rerun the page, and the run that wrote it may be cut short before the script arrives.
        st.session_state[SEEN] = RELOAD_PENDING
        reopen = f"url.searchParams.set('{REOPEN_CHAT}', '1');" if st.session_state.get("chat_fab") else ""
        st.html(_RELOAD % (reopen, RELOAD_DELAY_MS), unsafe_allow_javascript=True)
    else:
        st.session_state[SEEN] = fingerprint
    st.html(f"<style>{theme.css(tokens)}</style>")
