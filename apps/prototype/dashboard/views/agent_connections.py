"""Connection editors of the «AI agentlar» page: Odoo (address, database, login, API key) and an agent's
own Telegram bot (token, who may write to it). Every connection is checked before it is used; the key
or token is stored encrypted (core.secrets) and never shown again -- only its last 4 characters."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass

import streamlit as st

from assistant import odoo_tools
from core import secrets, state
from core import telegram as tg
from core.settings import Settings
from core.text import esc
from ui import components as c
from ui.i18n import t

log = logging.getLogger("dashboard.agents")

ODOO, TELEGRAM = "odoo", "telegram"
NEW = "__new__"


@dataclass
class Pending:
    """A checked connection that is not stored yet (stored when the agent is created)."""

    kind: str
    name: str
    config: dict
    secret: str
    status_text: str


def new_id() -> str:
    return f"k-{uuid.uuid4().hex[:8]}"


def store(settings: Settings, pending: Pending, connection_id: str | None = None) -> str:
    connection_id = connection_id or new_id()
    state.save_connection(settings, connection_id, pending.kind, pending.name, pending.config,
                          secrets.protect(pending.secret), "ok", pending.status_text)
    log.info("connection %s (%s) stored", connection_id, pending.kind)
    return connection_id


def _fingerprint(*values: str) -> str:
    return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()


def status_chip(row: dict | None) -> str:
    if not row:
        return c.chip(t("ag.conn.none"), "yellow")
    if row["status"] == "ok":
        return c.chip(t("ag.conn.ok"), "green")
    return c.chip(t(f"ag.conn.{row['status']}") if row["status"] in ("conflict", "error") else row["status"], "red")


# --------------------------------------------------------------------------- Odoo


def _odoo_label(row: dict) -> str:
    config = row["config"]
    return f"{config.get('login', '?')} @ {config.get('db', '?')} ({config.get('url', '').split('//')[-1]})"


def odoo_form(settings: Settings, key: str) -> Pending | None:
    """A new Odoo connection: four fields and «Tekshirish». Returns the checked connection."""
    with st.container(border=True):
        url = st.text_input(t("ag.odoo.url"), key=f"{key}-url", placeholder="https://kompaniya.odoo.com")
        left, right = st.columns(2)
        db = left.text_input(t("ag.odoo.db"), key=f"{key}-db")
        login = right.text_input(t("ag.odoo.login"), key=f"{key}-login")
        api_key = st.text_input(t("ag.odoo.key"), key=f"{key}-key", type="password")
        st.caption(t("ag.odoo.hint"))
        fingerprint = _fingerprint(url, db, login, api_key)
        if st.button(t("ag.check"), key=f"{key}-check", icon=":material/link:"):
            try:
                info = odoo_tools.Odoo(url, db, login, api_key).check()
                st.session_state[f"{key}-result"] = (fingerprint, info)
            except odoo_tools.OdooError as exc:
                st.session_state[f"{key}-result"] = (fingerprint, str(exc))
        result = st.session_state.get(f"{key}-result")
        if not result or result[0] != fingerprint:
            return None
        if isinstance(result[1], str):
            st.error(result[1], icon=":material/link_off:")
            return None
        info = result[1]
        text = t("ag.odoo.ok", user=info["user"], version=info["version"] or "?")
        st.success(text, icon=":material/check_circle:")
        config = {"url": odoo_tools.normalize_url(url), "db": db.strip(), "login": login.strip(), "user": info["user"],
                  "version": info["version"], "key_hint": secrets.mask(api_key)}
        return Pending(ODOO, f"{login.strip()} @ {db.strip()}", config, api_key, text)


def odoo_picker(settings: Settings, key: str, current: str | None = None) -> str | Pending | None:
    """An Odoo connection for an agent: one stored earlier (its id) or a new checked one (Pending)."""
    existing = state.connections(settings, ODOO)
    options = [row["id"] for row in existing] + [NEW]
    labels = {row["id"]: _odoo_label(row) for row in existing} | {NEW: t("ag.odoo.new")}
    index = options.index(current) if current in options else 0
    choice = st.selectbox(t("ag.odoo.pick"), options, index=index, format_func=labels.get, key=f"{key}-pick")
    if choice != NEW:
        row = next(item for item in existing if item["id"] == choice)
        st.html(f'<div class="db-small">{status_chip(row)} {c.chip(row["config"].get("key_hint", "••••"))}</div>')
        return choice
    return odoo_form(settings, f"{key}-new")


def recheck_odoo(settings: Settings, row: dict) -> tuple[bool, str]:
    """Checks a stored Odoo connection again and stores the result as its status."""
    config = row["config"]
    try:
        info = odoo_tools.Odoo(config.get("url", ""), config.get("db", ""), config.get("login", ""),
                               secrets.unprotect(row["secret"] or b"")).check()
    except (odoo_tools.OdooError, secrets.SecretsUnavailable) as exc:
        state.set_connection_status(settings, row["id"], "error", str(exc))
        return False, str(exc)
    text = t("ag.odoo.ok", user=info["user"], version=info["version"] or "?")
    state.set_connection_status(settings, row["id"], "ok", text)
    return True, text


# --------------------------------------------------------------------------- Telegram bot


def _allowed_text(config: dict) -> str:
    return ", ".join([*map(str, config.get("allowed_ids") or []), *[f"@{name}" for name in config.get("allowed_names") or []]])


def telegram_form(settings: Settings, key: str, except_connection: str | None = None) -> Pending | None:
    """A new bot for an agent: token, who may write, «Tekshirish». Returns the checked connection."""
    with st.container(border=True):
        st.caption(t("ag.tg.hint"))
        token = st.text_input(t("ag.tg.token"), key=f"{key}-token", type="password", placeholder="123456789:AA...")
        allowed = st.text_input(t("ag.tg.allowed"), key=f"{key}-allowed", help=t("ag.tg.allowed.help"))
        fingerprint = _fingerprint(token)
        if st.button(t("ag.check"), key=f"{key}-check", icon=":material/link:"):
            try:
                info = tg.check_token(token, settings.agents.protected_bots)
                used = [row for row in state.connections(settings, TELEGRAM)
                        if row["config"].get("bot_id") == info.id and row["id"] != except_connection]
                if used:
                    raise tg.TelegramError(409, t("ag.tg.used", username=info.username))
                st.session_state[f"{key}-result"] = (fingerprint, info)
            except tg.TelegramError as exc:
                st.session_state[f"{key}-result"] = (fingerprint, str(exc))
        result = st.session_state.get(f"{key}-result")
        if not result or result[0] != fingerprint:
            return None
        if isinstance(result[1], str):
            st.error(result[1], icon=":material/link_off:")
            return None
        info: tg.BotInfo = result[1]
        ids, names = tg.parse_allowed(allowed)
        st.success(t("ag.tg.ok", username=info.username), icon=":material/check_circle:")
        if not ids and not names:
            st.warning(t("ag.tg.nobody"), icon=":material/person_off:")
        config = {"username": info.username, "bot_id": info.id, "bot_name": info.name, "allowed_ids": ids,
                  "allowed_names": names, "key_hint": secrets.mask(token)}
        return Pending(TELEGRAM, f"@{info.username}", config, token, t("ag.tg.ok", username=info.username))


def telegram_panel(settings: Settings, key: str, row: dict) -> None:
    """A connected bot on the agent's page: status, who may write (editable), disconnect."""
    config = row["config"]
    st.html(f'<div class="db-small"><b>@{esc(config.get("username", ""))}</b> {status_chip(row)} '
            f'{c.chip(config.get("key_hint", "••••"))}</div>')
    if row["status"] != "ok" and row["status_text"]:
        st.warning(row["status_text"], icon=":material/warning:")
    with st.form(f"{key}-allowed-form", border=False):
        allowed = st.text_input(t("ag.tg.allowed"), value=_allowed_text(config), help=t("ag.tg.allowed.help"))
        left, right, _rest = st.columns([1.2, 1.4, 3])
        save = left.form_submit_button(t("ag.save"), icon=":material/save:")
        restart = right.form_submit_button(t("ag.tg.restart"), icon=":material/restart_alt:",
                                           disabled=row["status"] == "ok", help=t("ag.tg.restart.help"))
    if save or restart:
        ids, names = tg.parse_allowed(allowed)
        status, text = (row["status"], row["status_text"]) if save and not restart else ("ok", "")
        state.save_connection(settings, row["id"], TELEGRAM, row["name"],
                              {**config, "allowed_ids": ids, "allowed_names": names}, None, status, text)
        st.session_state["ag_flash"] = t("ag.tg.saved")
        st.rerun()
