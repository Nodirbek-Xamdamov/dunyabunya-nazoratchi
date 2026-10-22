"""Chat with the dashboard's AI assistant (assistant.orchestrator): a round button fixed in the
bottom-right corner of every page opens the chat panel (a popover that stays open while the assistant
works and the page reruns). Pictures (usually screenshots of the dashboard) can be attached to a
message. Undo / history / reset buttons work without the AI. The conversation, the pictures and every
layout change are stored in the dashboard's own state (core.state, core.images), so they survive page
reloads and restarts. The model SDKs are imported only when a message is actually sent."""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta

import streamlit as st

from core import images, state
from core import timeutil as tu
from core.settings import Settings
from core.text import esc
from ui import components as c
from ui import layout
from ui.i18n import t

log = logging.getLogger("dashboard.chat")

SHOWN = 6  # the panel stays small: the latest messages only (the full conversation is in a dialog)
CHANGE_CHARS = 90
DIALOG_SHOWN = 300
HISTORY_SHOWN = 30
BUTTON = "button"
ERROR = "error"
THUMB_PANEL, THUMB_DIALOG = 120, 280
_SYMBOLS = {"add": "+", "remove": "−", "move": "⇅", "rename": "✎", "edit": "✎", "undo": "↶",
            "reset": "⟲", "restore": "⟳", "other": "•"}


def page_id_for(nav_key: str) -> str:
    """The layout page the viewer is looking at (a group's / category's detail view has its own)."""
    if nav_key == "groups" and st.query_params.get("g"):
        return "groups.detail"
    if nav_key == "categories" and st.query_params.get("c"):
        return "categories.detail"
    return nav_key if nav_key in layout.PAGES or layout.is_own_page(nav_key) else "overview"


# --------------------------------------------------------------------------- describing changes


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _split_items(text: str) -> list[str]:
    """``A, B (x, y), C`` -> ["A", "B (x, y)", "C"] (commas inside brackets stay)."""
    items, depth, current = [], 0, ""
    for char in text:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        items.append(current.strip())
    return items


_ADD_VERBS = ("layout.shown", "qaytarildi")  # older summaries said "qaytarildi" / "yashirildi"
_REMOVE_VERBS = ("layout.hidden", "yashirildi")


def _page_verb(key: str) -> str:
    """The verb of a page summary such as «Hisobot» sahifasi yaratildi."""
    return t(key, page="").split(" sahifasi ", 1)[-1]


def _verb_kind(verb: str) -> str:
    verb = verb.strip()
    if verb in {t(key) if key.startswith("layout.") else key for key in _ADD_VERBS} | {
            t("layout.created"), _page_verb("layout.page_created")}:
        return "add"
    if verb in {t(key) if key.startswith("layout.") else key for key in _REMOVE_VERBS} | {
            _page_verb("layout.page_deleted")}:
        return "remove"
    if verb == t("layout.moved"):
        return "move"
    if verb == t("layout.renamed") or verb.endswith(t("layout.page_renamed", old="", new="").rsplit("»", 1)[-1]):
        return "rename"
    if verb == t("layout.updated"):
        return "edit"
    return "other"


def describe(summary: str, source: str = "chat") -> dict:
    """A stored change summary as {kind, page, verb, items}: kind is add / remove / move / rename /
    edit / undo / reset / restore / other; items are section names."""
    summary = str(summary or "")
    if source == state.UNDO:
        return {"kind": "undo", "page": "", "verb": t("chat.kind.undo"),
                "items": [part.strip() for part in summary.split("; ") if part.strip()]}
    if source in ("reset", "restore"):
        return {"kind": source, "page": "", "verb": summary, "items": []}
    if summary.startswith(t("layout.undone", summary="").strip()):
        return describe(summary[len(t("layout.undone", summary="")):], state.UNDO)
    if summary == t("layout.reset_all") or re.match(r"^«[^»]+» asl holatiga qaytarildi$", summary):
        return {"kind": "reset", "page": "", "verb": summary, "items": []}
    if summary.endswith(t("layout.restored", time="").strip()):
        return {"kind": "restore", "page": "", "verb": summary, "items": []}
    match = re.match(r"^«(?P<page>[^»]+)» sahifasi (?P<verb>.+)$", summary)
    if match:
        return {"kind": _verb_kind(match.group("verb")), "page": match.group("page"),
                "verb": f"{t('chat.kind.page')} {match.group('verb')}", "items": []}
    if ": " in summary and " — " in summary:
        page, rest = summary.split(": ", 1)
        verb, items = rest.split(" — ", 1)
        return {"kind": _verb_kind(verb), "page": page, "verb": verb, "items": _split_items(items)}
    return {"kind": "other", "page": "", "verb": summary, "items": []}


def _change_html(summary: str, compact: bool) -> str:
    info = describe(summary)
    if info["kind"] == "undo":
        body = esc(info["verb"]) + ": " + esc(_short("; ".join(info["items"]), CHANGE_CHARS) if compact
                                              else "; ".join(info["items"]))
    else:
        page = f'<b>{esc(info["page"])}</b> · ' if info["page"] else ""
        items = ", ".join(info["items"])
        body = page + esc(info["verb"]) + (" — " + esc(_short(items, CHANGE_CHARS) if compact else items) if items else "")
    return (f'<div class="db-change {info["kind"]}"><span class="db-change-icon">{_SYMBOLS[info["kind"]]}</span>'
            f'<span>{body}</span></div>')


# --------------------------------------------------------------------------- messages


def _day_label(day, today) -> str:
    if day == today:
        return t("chat.today")
    if day == today - timedelta(days=1):
        return t("chat.yesterday")
    return tu.fmt_day(day, with_year=day.year != today.year)


def _show(settings: Settings, message: dict, compact: bool = False) -> None:
    role = "user" if message.get("role") == "user" else "assistant"
    with st.chat_message(role):
        text = str(message.get("text") or "")
        changes = message.get("changes") or []
        if text and text.strip() not in changes:  # a button's message is just its change: shown once, as a badge
            st.markdown(c.md_escape(text) if role == "user" else text)
        pictures = [picture for picture in (images.load(settings, name) for name in message.get("files") or []) if picture]
        if pictures:
            with st.container(horizontal=True, gap="small"):
                for picture in pictures:
                    st.image(picture.data, width=THUMB_PANEL if compact else THUMB_DIALOG)
        if changes:
            st.html("".join(_change_html(change, compact) for change in changes))
        when = state.local_time(message.get("created_at") or "", settings.tz)
        if when is not None:
            st.html(f'<div class="db-msg-time">{when:%H:%M}</div>')


def _log_button(settings: Settings, summary: str) -> None:
    """Button actions also go into the chat, so the conversation (and the assistant) sees them."""
    state.chat_add(settings, "assistant", summary, agents=BUTTON, changes=[summary])


@st.dialog(t("chat.dialog.title"), width="large")
def _dialog(settings: Settings) -> None:
    messages = state.chat_recent(settings, DIALOG_SHOWN)
    if not messages:
        st.caption(t("chat.empty"))
    today, last_day = datetime.now(settings.tz).date(), None
    for message in messages:
        when = state.local_time(message.get("created_at") or "", settings.tz)
        if when is not None and when.date() != last_day:
            last_day = when.date()
            st.html(f'<div class="db-day-sep"><span>{esc(_day_label(last_day, today))}</span></div>')
        _show(settings, message)


# --------------------------------------------------------------------------- sending a message


def _parse(value) -> tuple[str, list]:
    """chat_input returns a str, or a ChatInputValue (text + files) when files are accepted."""
    if value is None:
        return "", []
    if isinstance(value, str):
        return value.strip(), []
    return str(getattr(value, "text", "") or "").strip(), list(getattr(value, "files", None) or [])


def _store_pictures(settings: Settings, uploads: list) -> tuple[list, list[str]]:
    pictures, problems = [], []
    for upload in uploads[: images.MAX_FILES]:
        try:
            pictures.append(images.store(settings, upload.getvalue()))
        except images.ImageError as exc:
            problems.append(t("chat.picture.bad", name=getattr(upload, "name", "?"), reason=str(exc)))
    if len(uploads) > images.MAX_FILES:
        problems.append(t("chat.picture.too_many", n=images.MAX_FILES))
    return pictures, problems


def _ask(settings: Settings, ctx, page_id: str, text: str, uploads: list, box) -> None:
    from assistant import orchestrator

    chat = settings.chat
    if chat.daily_limit and state.chat_count_since(settings, state.local_midnight_utc(settings.tz)) >= chat.daily_limit:
        with box:
            st.warning(t("chat.limit", n=chat.daily_limit))
        return
    pictures, problems = _store_pictures(settings, uploads)
    if not text and not pictures:
        with box:
            st.warning(" ".join(problems) or t("chat.picture.none"))
        return
    history = state.chat_recent(settings, chat.context_messages)
    text = text or t("chat.picture.only")
    message_id = state.chat_add(settings, "user", text, files=[picture.name for picture in pictures])
    if problems:
        state.chat_add(settings, "assistant", " ".join(problems), agents=orchestrator.ERROR_AGENT)
    context = {"group_id": f"chat-{message_id}", "page_id": page_id, "period": ctx.period, "now": ctx.now}
    reply, error = None, ""
    with box:
        _show(settings, {"role": "user", "text": text, "files": [picture.name for picture in pictures]}, compact=True)
        with st.chat_message("assistant"):
            with st.status(t("chat.working"), expanded=False) as status:
                try:
                    reply = orchestrator.handle(settings, text, history, context, pictures=pictures,
                                                on_step=lambda label: status.update(label=label))
                except orchestrator.AgentError as exc:
                    error = str(exc)
                except Exception:  # noqa: BLE001 -- the chat must not break the page
                    log.exception("assistant failed")
                    error = t("chat.err.internal")
                status.update(label=t("chat.step.done") if reply else error[:80], state="complete" if reply else "error")
    if reply is None:
        state.chat_add(settings, "assistant", error, agents=orchestrator.ERROR_AGENT)
    else:
        state.chat_add(settings, "assistant", reply.text, agents=",".join(reply.agents), changes=reply.changes,
                       request_ids=reply.usage.request_ids, input_tokens=reply.usage.input_tokens,
                       output_tokens=reply.usage.output_tokens)
    st.rerun()


# --------------------------------------------------------------------------- undo, history, reset


def _undo(settings: Settings) -> None:
    summary = state.undo(settings, group_id=state.new_group(BUTTON))
    if summary is None:
        st.toast(t("chat.undo.none"), icon=":material/info:")
        return
    _log_button(settings, t("layout.undone", summary=summary))
    st.rerun()


def _timeline_html(version: dict, settings: Settings, current: bool) -> str:
    info = describe(version["summary"], version["source"])
    when = state.local_time(version["created_at"], settings.tz)
    head = [f'<span class="db-tl-time">{when:%H:%M}</span>' if when else ""]
    if info["page"]:
        head.append(c.chip(info["page"], "brand"))
    head.append(f'<span class="db-tl-verb">{esc(info["verb"])}</span>')
    source_key = {"chat": "chat.source.chat", BUTTON: "chat.source.button"}.get(version["source"])
    if source_key:
        head.append(f'<span class="db-tl-src">{esc(t(source_key))}</span>')
    if current:
        head.append(c.chip(t("chat.history.current"), "green"))
    items = info["items"]
    if info["kind"] == "undo":
        body = "".join(f'<div class="db-tl-sub">{_change_html(item, compact=False)}</div>' for item in items)
    else:
        body = "".join(c.chip(item) for item in items)
    return (f'<div class="db-tl-item {info["kind"]}"><div class="db-tl-icon">{_SYMBOLS[info["kind"]]}</div>'
            f'<div class="db-tl-body"><div class="db-tl-head">{"".join(head)}</div>'
            f'<div class="db-tl-items">{body}</div></div></div>')


@st.dialog(t("chat.history.title"), width="large")
def _history(settings: Settings) -> None:
    versions = state.history(settings, HISTORY_SHOWN)
    if not versions:
        st.caption(t("chat.history.empty"))
        return
    st.caption(t("chat.history.note"))
    today, last_day = datetime.now(settings.tz).date(), None
    for index, version in enumerate(versions):
        when = state.local_time(version["created_at"], settings.tz)
        if when is not None and when.date() != last_day:
            last_day = when.date()
            st.html(f'<div class="db-day-sep"><span>{esc(_day_label(last_day, today))}</span></div>')
        left, right = st.columns([5, 1.3], vertical_alignment="center")
        with left:
            st.html(_timeline_html(version, settings, current=index == 0))
        with right:
            if index > 0 and st.button(t("chat.history.restore"), key=f"chat_restore_{version['id']}", type="tertiary",
                                       icon=":material/settings_backup_restore:", help=t("chat.history.restore.help")):
                summary = t("layout.restored", time=tu.fmt_dt(when))
                if state.restore(settings, version["id"], group_id=state.new_group(BUTTON), summary=summary):
                    _log_button(settings, summary)
                st.rerun()


@st.dialog(t("chat.reset.title"))
def _reset_dialog(settings: Settings) -> None:
    st.write(t("chat.reset.note"))
    if st.button(t("chat.reset.confirm"), key="chat_reset", type="primary", width="stretch"):
        _reset(settings)


def _reset(settings: Settings) -> None:
    summary = state.update_layout(settings, lambda overlay: layout.reset(overlay, "all"),
                                  group_id=state.new_group(BUTTON), source="reset")
    if summary is None:
        st.toast(t("chat.reset.none"), icon=":material/info:")
        return
    _log_button(settings, summary)
    st.rerun()


def _buttons(settings: Settings) -> None:
    with st.container(horizontal=True, gap="small", key="chat-tools"):
        if st.button(t("chat.undo"), icon=":material/undo:", help=t("chat.undo.help"), key="chat_undo",
                     type="tertiary"):
            _undo(settings)
        if st.button("", icon=":material/history:", help=t("chat.history.help"), key="chat_history",
                     type="tertiary"):
            _history(settings)
        if st.button("", icon=":material/restart_alt:", help=t("chat.reset.help"), key="chat_reset_open",
                     type="tertiary"):
            _reset_dialog(settings)
        if st.button("", icon=":material/open_in_full:", help=t("chat.expand.help"), key="chat_expand",
                     type="tertiary"):
            _dialog(settings)


# --------------------------------------------------------------------------- the panel


def panel(settings: Settings, ctx, nav_key: str) -> None:
    """The round chat button in the bottom-right corner and the chat panel it opens (ui/style.css pins
    the ``chat_fab`` popover there). The open/closed state lives in Session State, so the panel stays
    open across the reruns a chat message causes."""
    page_id = page_id_for(nav_key)
    with st.popover("", icon=":material/forum:", help=t("chat.fab.help"), key="chat_fab", on_change="rerun"):
        st.html(f'<div class="db-chat-title">{esc(t("chat.title"))}</div>')
        try:
            messages = state.chat_recent(settings, SHOWN)
            box = st.container(height=300 if len(messages) > 2 else "content", border=False, autoscroll=True,
                               key="chat-box")
            with box:
                if not messages:
                    st.caption(t("chat.empty"))
                for message in messages:
                    _show(settings, message, compact=True)
            chat = settings.chat
            if not chat.enabled:
                st.caption(t("chat.disabled"))
            elif not chat.api_key:
                st.caption(t("chat.not_configured", name=f"{chat.provider.upper()}_API_KEY"))
            else:
                value = st.chat_input(t("chat.placeholder"), key="chat_input", max_chars=1500, accept_file="multiple",
                                      file_type=list(images.FILE_TYPES), max_upload_size=images.MAX_BYTES // 1024 // 1024)
                text, uploads = _parse(value)
                if text or uploads:
                    _ask(settings, ctx, page_id, text, uploads, box)
            _buttons(settings)
        except (sqlite3.Error, OSError) as exc:
            log.warning("chat state unavailable: %s", type(exc).__name__)
            st.caption(t("error.data"))
