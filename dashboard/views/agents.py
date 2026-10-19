"""AI agentlar: the chat assistant's agents as a tree (Boshqaruvchi on top, the three specialists it
calls below). A click on an agent opens its settings: what it does, its model and tools, and its system
prompt -- edited here, put back to the default or restored from its history (core.state.agent_prompts).
Only the instructions part of a prompt is edited; the reference part (catalog, data sources,
definitions, CSS selectors) is always written by the dashboard (assistant.prompts), so the tools keep
working after any edit. Prompt text never goes to the logs."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import streamlit as st

from assistant import prompts
from core import state
from core import timeutil as tu
from core.settings import Settings
from ui import components as c
from ui import layout, overrides, shell
from ui.i18n import t
from views.common import PageData

log = logging.getLogger("dashboard.agents")

QP_AGENT = "a"
HISTORY_ROWS = 10
_FLASH = "ag_flash"


@dataclass(frozen=True)
class Agent:
    id: str
    icon: str  # Material Symbols name
    parent: str | None

    @property
    def title(self) -> str:
        return t(f"ag.{self.id}")

    @property
    def role(self) -> str:
        return t(f"ag.{self.id}.role")


AGENTS = {agent.id: agent for agent in (
    Agent("orchestrator", "hub", None),
    Agent("data", "query_stats", "orchestrator"),
    Agent("view", "dashboard_customize", "orchestrator"),
    Agent("design", "palette", "orchestrator"),
)}
ROOT = "orchestrator"
assert tuple(AGENTS) == prompts.AGENTS


def children(agent_id: str) -> list[Agent]:
    return [agent for agent in AGENTS.values() if agent.parent == agent_id]


def tools(agent_id: str, settings: Settings) -> list:
    """The agent's tools (names and descriptions; nothing runs)."""
    from assistant import data_agent, design_agent, orchestrator, ui_agent  # noqa: PLC0415 -- they import views

    if agent_id == "orchestrator":
        return orchestrator.describe_tools()
    module = {"data": data_agent, "view": ui_agent, "design": design_agent}[agent_id]
    return module.describe_tools(settings)


def _open(agent_id: str | None) -> None:
    shell.set_param(QP_AGENT, agent_id)
    st.rerun()


def _when(row: dict, settings: Settings) -> str:
    moment = state.local_time(row["created_at"], settings.tz)
    return tu.fmt_dt(moment, True) if moment else row["created_at"]


def _status_chip(saved: dict | None) -> str:
    return c.chip(t("ag.status.custom"), "brand") if saved else c.chip(t("ag.status.default"))


# --------------------------------------------------------------------------- the tree


def _node(agent: Agent, settings: Settings, root: bool = False) -> None:
    """One agent card; the whole card is a button that opens the agent's settings."""
    saved = prompts.saved_instructions(agent.id, settings)
    with st.container(border=True, key=f"agnode-{agent.id}"):
        st.subheader(f":material/{agent.icon}: {agent.title}", anchor=False)
        st.caption(agent.role)
        chips = _status_chip(saved) + c.chip(t("ag.tools", n=len(tools(agent.id, settings))))
        if root:
            chips += c.chip(t("ag.calls", n=len(children(agent.id))))
        st.html(f'<div class="db-ag-chips">{chips}<span class="db-ag-more">{t("ag.hint")}</span></div>')
        if st.button(t("ag.open", name=agent.title), key=f"agopen-{agent.id}"):
            _open(agent.id)


def _tree(d: PageData) -> None:
    settings = d.settings
    _left, middle, _right = st.columns([1, 1.25, 1])
    with middle:
        _node(AGENTS[ROOT], settings, root=True)
    leaves = children(ROOT)
    st.html('<div class="db-tree-link" aria-hidden="true"><div class="db-tree-stem"></div>'
            '<div class="db-tree-bar"></div>' + '<span class="db-tree-drop"></span>' * len(leaves) + '</div>')
    for column, agent in zip(st.columns(len(leaves), gap="small"), leaves):
        with column:
            _node(agent, settings)
    st.caption(t("ag.guard"))


TREE = layout.Block("ag.tree", "blk.ag.tree", _tree, hideable=False)
PAGE = layout.register(layout.Page("agents", "page.agents", "agents", (layout.Place(TREE),),
                                   accepts_portable=False))


# --------------------------------------------------------------------------- one agent's settings


def _flash() -> None:
    message = st.session_state.pop(_FLASH, None)
    if message:
        st.success(message, icon=":material/check_circle:")


def _store(settings: Settings, agent: Agent, text: str, action: str, message: str) -> None:
    state.set_agent_prompt(settings, agent.id, text, action)
    log.info("agent %s prompt: %s (%d characters)", agent.id, action, len(text))
    st.session_state[_FLASH] = message
    st.rerun()


def _save(settings: Settings, agent: Agent, text: str, current: str) -> None:
    text = text.strip()
    if not text:
        st.warning(t("ag.err.empty"))
        return
    if text == current.strip():
        st.info(t("ag.nochange"))
        return
    if text == prompts.DEFAULTS[agent.id].strip():  # the default typed back in: store it as the default
        _store(settings, agent, "", "reset", t("ag.reset.done"))
    else:
        _store(settings, agent, text, "save", t("ag.saved"))


def _summary(agent: Agent, settings: Settings, saved: dict | None, tool_count: int) -> None:
    cols = st.columns(3)
    with cols[0]:
        c.kpi(t("ag.model"), settings.chat.model, help=t("ag.model.help", provider=settings.chat.provider))
    with cols[1]:
        c.kpi(t("ag.tools.count"), str(tool_count))
    with cols[2]:
        c.kpi(t("ag.status"), t("ag.status.custom.value") if saved else t("ag.status.default.value"),
              help=t("ag.status.since", when=_when(saved, settings)) if saved else None)
    related = [AGENTS[agent.parent]] if agent.parent else children(agent.id)
    label = t("ag.parent") if agent.parent else t("ag.children")
    st.caption(label)
    for column, other in zip(st.columns(max(len(related), 3)), related):
        with column:
            if st.button(other.title, icon=f":material/{other.icon}:", key=f"agrel-{other.id}", width="stretch"):
                _open(other.id)


def _editor(agent: Agent, settings: Settings, saved: dict | None) -> None:
    c.section(t("ag.prompt"), t("ag.prompt.caption"))
    current = saved["text"] if saved else prompts.DEFAULTS[agent.id]
    version = saved["id"] if saved else 0
    with st.form(f"agform-{agent.id}", border=False, enter_to_submit=False):
        text = st.text_area(t("ag.prompt.label"), value=current, height=420, max_chars=prompts.MAX_INSTRUCTIONS,
                            key=f"agtext-{agent.id}-{version}", label_visibility="collapsed")
        save_col, reset_col, _rest = st.columns([1, 1.9, 3.5])
        with save_col:
            submitted = st.form_submit_button(t("ag.save"), type="primary", icon=":material/save:", width="stretch")
        with reset_col:
            reset = st.form_submit_button(t("ag.reset"), icon=":material/restart_alt:", key=f"agreset-{agent.id}",
                                          disabled=saved is None, help=t("ag.reset.help"), width="stretch")
    if submitted:
        _save(settings, agent, text, current)
    if reset:
        _store(settings, agent, "", "reset", t("ag.reset.done"))


def _reference(agent: Agent, settings: Settings) -> None:
    ref = prompts.reference(agent.id, settings)
    with st.expander(t("ag.reference"), icon=":material/lock:"):
        if not ref:
            st.caption(t("ag.reference.none"))
            return
        st.caption(t("ag.reference.caption"))
        st.code(ref, language=None, wrap_lines=True)


def _tools(tool_list: list) -> None:
    with st.expander(t("ag.tools.title", n=len(tool_list)), icon=":material/build:"):
        st.markdown("\n".join(f"- `{tool.name}` — {c.md_escape(tool.description)}" for tool in tool_list))


def _history(agent: Agent, settings: Settings, saved: dict | None) -> None:
    c.section(t("ag.history"))
    rows = state.agent_prompt_history(settings, agent.id, HISTORY_ROWS)
    if not rows:
        st.caption(t("ag.history.empty"))
        return
    for index, row in enumerate(rows):
        text = row["text"] or prompts.DEFAULTS[agent.id]
        with st.container(key=f"aghist-{row['id']}"):
            info, view, restore = st.columns([5, 1.2, 1.2], vertical_alignment="center")
            with info:
                label = t(f"ag.action.{row['action']}") if row["action"] in ("save", "reset", "restore") else row["action"]
                current = f" · **{t('ag.history.current')}**" if index == 0 else ""
                default = f" · {t('ag.status.default')}" if not row["text"] else ""
                st.markdown(f"{_when(row, settings)} — {label}{default} · {t('ag.chars', n=len(text))}{current}")
            with view:
                with st.popover(t("ag.history.view"), icon=":material/visibility:", width="stretch"):
                    st.code(text, language=None, wrap_lines=True)
            with restore:
                if index > 0 and st.button(t("ag.history.restore"), icon=":material/history:",
                                           key=f"agrestore-{row['id']}", width="stretch"):
                    _store(settings, agent, row["text"], "restore", t("ag.restored"))


def _detail(agent: Agent, settings: Settings) -> None:
    if st.button(t("ag.back"), icon=":material/arrow_back:", key="agback"):
        _open(None)
    st.title(f":material/{agent.icon}: {agent.title}", anchor=False)
    st.caption(agent.role)
    _flash()
    saved = prompts.saved_instructions(agent.id, settings)
    tool_list = tools(agent.id, settings)
    _summary(agent, settings, saved, len(tool_list))
    _editor(agent, settings, saved)
    _reference(agent, settings)
    _tools(tool_list)
    _history(agent, settings, saved)


@shell.guard
def render() -> None:
    ctx = shell.current()
    agent_id = st.query_params.get(QP_AGENT)
    if agent_id:
        agent = AGENTS.get(agent_id)
        if agent is None:
            st.warning(t("ag.unknown"))
            if st.button(t("ag.back"), icon=":material/arrow_back:", key="agback"):
                _open(None)
            return
        _detail(agent, ctx.settings)
        return
    st.title(overrides.page_title(t("ag.title")), anchor=False)
    st.caption(t("ag.caption"))
    layout.render_page(PAGE, PageData(ctx))
