"""AI agentlar: the chat assistant's agents as a tree -- Boshqaruvchi on top, the agents it calls below,
their own sub-agents under them (at most three levels, assistant.registry). «Agent qo'shish» creates a
new agent (views/agent_builder.py). A click on an agent opens its settings: the model (changeable), and
for a created agent «Sinab ko'rish» (a test conversation with it, never stored), its place in the tree,
tool groups, its task (the designer can rewrite the prompt from a changed task), connections (Odoo, its
own Telegram bot) and deleting it; for every agent the system prompt -- edited, put back to the default
or restored from its history (core.state.agent_prompts). Only the instructions part of a prompt is edited; the reference part
is always written by the dashboard (assistant.prompts), so the tools keep working after any edit.
Prompt text and keys never go to the logs."""

from __future__ import annotations

import logging
import time

import streamlit as st

from assistant import builder, llm, models, prompts, registry
from core import state
from core import timeutil as tu
from core.settings import Settings
from ui import components as c
from ui import layout, overrides, shell
from ui.i18n import t
from views import agent_builder
from views import agent_connections as conn
from views.common import PageData

log = logging.getLogger("dashboard.agents")

QP_AGENT = "a"
NEW = "new"
HISTORY_ROWS = 10
ROW_SIZE = 4  # agents per row under Boshqaruvchi
_FLASH = "ag_flash"
_TRY = "ag_try"  # session state: agent id -> the test conversation of «Sinab ko'rish» (never stored)
TRY_HISTORY = 12  # earlier test turns the agent sees
_REDESIGN = "ag_redesign"  # session state: agent id -> the designer's proposal for a changed task


def tools(agent_id: str, settings: Settings, agents: dict) -> list:
    """The agent's tools, including the ones that call the created agents below it (names and
    descriptions only; nothing runs)."""
    from assistant import data_agent, orchestrator, team, ui_agent  # noqa: PLC0415 -- they import views

    call = team.Call(settings, llm.pool(None), {"group_id": "-"}, agents=agents)
    agent = agents[agent_id]
    if agent.custom:
        own = team.group_tools(call, agent, [])
    elif agent_id == registry.ROOT:
        own = orchestrator.describe_tools()
    else:
        own = {"data": data_agent, "view": ui_agent}[agent_id].describe_tools(settings)
    return [*own, *team.child_tools(call, agent_id)]


def _open(agent_id: str | None) -> None:
    shell.set_param(QP_AGENT, agent_id)
    st.rerun()


def _when(row: dict, settings: Settings) -> str:
    moment = state.local_time(row["created_at"], settings.tz)
    return tu.fmt_dt(moment, True) if moment else row["created_at"]


def _flash() -> None:
    message = st.session_state.pop(_FLASH, None)
    if message:
        st.success(message, icon=":material/check_circle:")


def _status_chip(saved: dict | None) -> str:
    return c.chip(t("ag.status.custom"), "brand") if saved else c.chip(t("ag.status.default"))


def _connection_chips(agent: registry.Agent, settings: Settings) -> str:
    if not agent.custom:
        return ""
    record, chips = agent.record or {}, ""
    telegram = state.connection(settings, record.get("telegram_connection"))
    if telegram:
        chips += c.chip(f"@{telegram['config'].get('username', '')}", "green" if telegram["status"] == "ok" else "red")
    if "odoo" in agent.tool_groups:
        odoo = state.connection(settings, record.get("odoo_connection"))
        chips += c.chip("Odoo", "green") if odoo and odoo["status"] == "ok" else c.chip(t("ag.odoo.missing"), "yellow")
    return chips


# --------------------------------------------------------------------------- the tree


def _node(agent: registry.Agent, settings: Settings, agents: dict, nested: bool) -> None:
    """One agent card (the whole card is a button that opens the agent's settings); under it its own
    sub-agents when they are drawn nested (third level)."""
    saved = prompts.saved_instructions(agent.id, settings)
    kids = registry.children(agents, agent.id)
    with st.container(border=True, key=f"agnode-{agent.id}"):
        st.subheader(f":material/{agent.icon}: {c.md_escape(agent.name)}", anchor=False)
        if agent.role:
            st.caption(agent.role)
        chips = (_status_chip(saved) + c.chip(agent.model) + c.chip(t("ag.tools", n=len(tools(agent.id, settings, agents))))
                 + (c.chip(t("ag.calls", n=len(kids))) if kids else "") + _connection_chips(agent, settings))
        st.html(f'<div class="db-ag-chips">{chips}<span class="db-ag-more">{t("ag.hint")}</span></div>')
        if st.button(t("ag.open", name=agent.name), key=f"agopen-{agent.id}"):
            _open(agent.id)
    if nested and kids:
        with st.container(key=f"agkids-{agent.id}"):
            for kid in kids:
                _node(kid, settings, agents, nested=True)


def _tree(d: PageData) -> None:
    settings = d.settings
    agents = registry.load(settings)
    _left, middle, _right = st.columns([1, 1.25, 1])
    with middle:
        _node(agents[registry.ROOT], settings, agents, nested=False)
    row = registry.children(agents, registry.ROOT)
    st.html('<div class="db-tree-stem" aria-hidden="true"></div>')
    for start in range(0, len(row), ROW_SIZE):
        chunk = row[start:start + ROW_SIZE]
        width = ROW_SIZE if len(row) > ROW_SIZE else len(chunk)
        for index, (column, agent) in enumerate(zip(st.columns(width, gap="small"), chunk)):
            place = "only" if len(chunk) == 1 and start == 0 else ("first" if index == 0 else
                                                                   "last" if index == len(chunk) - 1 else "mid")
            with column:
                st.html(f'<div class="db-tree-drop {place}{" more" if start else ""}" aria-hidden="true"></div>')
                _node(agent, settings, agents, nested=True)
    if not any(agent.custom for agent in agents.values()):
        st.info(t("ag.tree.empty"), icon=":material/add_circle:")
    st.caption(t("ag.guard"))


TREE = layout.Block("ag.tree", "blk.ag.tree", _tree, hideable=False)
PAGE = layout.register(layout.Page("agents", "page.agents", "agents", (layout.Place(TREE),),
                                   accepts_portable=False))


# --------------------------------------------------------------------------- one agent's settings


def _store_prompt(settings: Settings, agent: registry.Agent, text: str, action: str, message: str) -> None:
    state.set_agent_prompt(settings, agent.id, text, action)
    log.info("agent %s prompt: %s (%d characters)", agent.id, action, len(text))
    st.session_state[_FLASH] = message
    st.rerun()


def _save_prompt(settings: Settings, agent: registry.Agent, text: str, current: str) -> None:
    text = text.strip()
    if not text:
        st.warning(t("ag.err.empty"))
        return
    if text == current.strip():
        st.info(t("ag.nochange"))
        return
    if text == prompts.default_instructions(agent.id, settings).strip():  # the default typed back in
        _store_prompt(settings, agent, "", "reset", t("ag.reset.done"))
    else:
        _store_prompt(settings, agent, text, "save", t("ag.saved"))


def _set_model(settings: Settings, agent: registry.Agent, key: str) -> None:
    """on_change of the model box: stored at once."""
    value = st.session_state.get(key) or ""
    if agent.custom:
        state.save_custom_agent(settings, {**agent.record, "model": value})
    else:
        state.set_agent_model(settings, agent.id, value or None)
    log.info("agent %s model: %s", agent.id, value or "default")
    st.session_state[_FLASH] = t("ag.model.saved", model=value or settings.chat.model)


def _summary(agent: registry.Agent, settings: Settings, agents: dict, saved: dict | None, tool_count: int) -> None:
    options = [model.id for model in models.available(settings)]
    if not agent.custom:
        options = ["", *options]  # "" = the default model from dashboard\.env
    current = agent.model if (agent.custom or agent.model_chosen) else ""
    key = f"agmodel-{agent.id}"
    cols = st.columns(3, vertical_alignment="bottom")
    with cols[0]:
        st.selectbox(t("ag.model"), options, index=options.index(current) if current in options else 0, key=key,
                     format_func=lambda value: t("ag.model.default", model=settings.chat.model) if not value
                     else models.label(value), on_change=_set_model, args=(settings, agent, key),
                     help=t("ag.model.help", provider=models.provider_of(agent.model)))
    with cols[1]:
        c.kpi(t("ag.tools.count"), str(tool_count))
    with cols[2]:
        c.kpi(t("ag.status"), t("ag.status.custom.value") if saved else t("ag.status.default.value"),
              help=t("ag.status.since", when=_when(saved, settings)) if saved else None)
    if not models.has_key(settings, "anthropic"):
        st.caption(t("ag.model.claude_hint"))
    parent = agents.get(agent.parent or "")
    kids = registry.children(agents, agent.id)
    for label, related in ((t("ag.parent"), [parent] if parent else []), (t("ag.children"), kids)):
        if not related:
            continue
        st.caption(label)
        for column, other in zip(st.columns(max(len(related), 3)), related):
            with column:
                if st.button(other.name, icon=f":material/{other.icon}:", key=f"agrel-{other.id}", width="stretch"):
                    _open(other.id)


# --------------------------------------------------------------------------- «Sinab ko'rish»


def _try_ask(agent: registry.Agent, settings: Settings, agents: dict, text: str, turns: list[dict]) -> dict:
    """One test message to the agent, run exactly like the chat runs it (its model, tools and sub-agents)."""
    from assistant import team  # noqa: PLC0415 -- it imports the views through the view agent

    ctx = shell.current()
    context = {"group_id": state.new_group("try"), "page_id": "agents", "period": ctx.period, "now": ctx.now}
    call = team.Call(settings, llm.pool(None), context, agents=agents)
    started = time.monotonic()
    history = [{"role": turn["role"], "text": turn["text"]} for turn in turns if not turn.get("error")][-TRY_HISTORY:]
    try:
        answer = team.run_custom(call, agent, text, history)
    except llm.AgentError as exc:
        return {"role": "assistant", "text": str(exc), "error": True}
    except Exception:  # noqa: BLE001 -- the page must not break
        log.exception("agent %s: test message failed", agent.id)
        return {"role": "assistant", "text": t("chat.err.internal"), "error": True}
    log.info("agent %s: test message answered (tools=%s, tokens in=%d out=%d)", agent.id, ",".join(call.tool_calls),
             call.usage.input_tokens, call.usage.output_tokens)
    return {"role": "assistant", "text": answer, "seconds": time.monotonic() - started, "tools": call.tool_calls,
            "agents": [agents[item].name for item in call.used[1:] if item in agents], "changes": call.changes,
            "tokens": call.usage.input_tokens + call.usage.output_tokens}


def _try_meta(turn: dict) -> str:
    parts = [t("ag.try.meta.time", seconds=f"{turn.get('seconds', 0):.1f}".replace(".", ","))]
    tools = list(dict.fromkeys(turn.get("tools") or []))
    parts.append(t("ag.try.meta.tools", tools=", ".join(tools)) if tools else t("ag.try.meta.none"))
    if turn.get("agents"):
        parts.append(t("ag.try.meta.agents", names=", ".join(turn["agents"])))
    parts.append(t("ag.try.meta.tokens", n=tu.fmt_int(turn.get("tokens") or 0)))
    if turn.get("changes"):
        parts.append(t("ag.try.meta.changes", changes="; ".join(turn["changes"])))
    return " · ".join(parts)


def _try_out(agent: registry.Agent, settings: Settings, agents: dict) -> None:
    c.section(t("ag.try"), t("ag.try.caption"))
    turns = st.session_state.setdefault(_TRY, {}).setdefault(agent.id, [])
    for turn in turns:
        with st.chat_message("user" if turn["role"] == "user" else "assistant"):
            if turn["role"] == "user":
                st.markdown(c.md_escape(turn["text"]))
            elif turn.get("error"):
                st.error(turn["text"], icon=":material/error:")
            else:
                st.markdown(turn["text"])
                st.caption(_try_meta(turn))
    with st.form(f"agtry-{agent.id}", clear_on_submit=True, border=True):
        text = st.text_area(t("ag.try.label"), height=90, max_chars=2000, placeholder=t("ag.try.placeholder"),
                            key=f"agtry-text-{agent.id}", label_visibility="collapsed")
        left, right, _rest = st.columns([1.2, 1.2, 3.6])
        send = left.form_submit_button(t("ag.try.send"), type="primary", icon=":material/send:", width="stretch")
        clear = right.form_submit_button(t("ag.try.clear"), icon=":material/delete_sweep:", disabled=not turns,
                                         width="stretch")
    if clear:
        turns.clear()
        st.rerun()
    if send:
        text = text.strip()
        if not text:
            st.warning(t("ag.try.empty"))
            return
        with st.spinner(t("ag.try.working", name=agent.name)):
            reply = _try_ask(agent, settings, agents, text, turns)
        turns += [{"role": "user", "text": text}, reply]
        st.rerun()


# --------------------------------------------------------------------------- a created agent's settings


def _custom_settings(agent: registry.Agent, settings: Settings, agents: dict) -> None:
    record = agent.record or {}
    c.section(t("ag.settings"), t("ag.settings.caption"))
    with st.form(f"agset-{agent.id}", border=True):
        name = st.text_input(t("ag.new.name"), value=agent.name, max_chars=60)
        summary = st.text_area(t("ag.summary.label"), value=record.get("summary", ""), height=70)
        parents = registry.parent_options(agents, agent.id)
        parent = st.selectbox(t("ag.parent.pick"), parents, format_func=lambda agent_id: agents[agent_id].name,
                              index=parents.index(agent.parent) if agent.parent in parents else 0)
        st.markdown(f"**{t('ag.tools.pick')}**")
        groups = []
        for column, group in zip(st.columns(len(registry.TOOL_GROUPS)), registry.TOOL_GROUPS.values()):
            with column:
                if st.checkbox(t(f"ag.group.{group.id}"), value=group.id in agent.tool_groups,
                               help=t(f"ag.group.{group.id}.help")):
                    groups.append(group.id)
        routing = st.text_area(t("ag.routing.label"), value=record.get("routing", ""), height=70)
        submitted = st.form_submit_button(t("ag.save"), icon=":material/save:", type="primary")
    if submitted:
        if not name.strip():
            st.warning(t("ag.new.err.name"))
            return
        state.save_custom_agent(settings, {**record, "name": name.strip(), "summary": summary.strip(), "parent": parent,
                                           "tool_groups": groups, "routing": routing.strip() or record.get("routing", ""),
                                           "odoo_connection": record.get("odoo_connection") if "odoo" in groups else None})
        log.info("agent %s settings saved (parent=%s, groups=%s)", agent.id, parent, ",".join(groups))
        st.session_state[_FLASH] = t("ag.settings.saved")
        st.rerun()


def _redesign(agent: registry.Agent, settings: Settings, agents: dict, task: str) -> None:
    task = task.strip()
    if len(task) < agent_builder.MIN_TASK:
        st.warning(t("ag.new.err.task", n=agent_builder.MIN_TASK))
        return
    others = {agent_id: item for agent_id, item in agents.items() if agent_id != agent.id}  # not a duplicate of itself
    with st.spinner(t("ag.new.working")):
        try:
            draft = builder.design(settings, agent.name, task, others)
        except llm.AgentError as exc:
            st.error(str(exc))
            return
        except Exception:  # noqa: BLE001 -- the page must not break
            log.exception("agent %s: redesign failed", agent.id)
            st.error(t("chat.err.internal"))
            return
    log.info("agent %s redesigned: model=%s groups=%s tokens=%d/%d", agent.id, draft.model, ",".join(draft.tool_groups),
             draft.usage.input_tokens, draft.usage.output_tokens)
    st.session_state.setdefault(_REDESIGN, {})[agent.id] = draft
    st.session_state[f"agrd-no-{agent.id}"] = st.session_state.get(f"agrd-no-{agent.id}", 0) + 1
    st.rerun()


def _task(agent: registry.Agent, settings: Settings, agents: dict) -> None:
    """The agent's task; the designer rewrites the prompt from a changed task (the old prompt stays in
    the history)."""
    record = agent.record or {}
    c.section(t("ag.task"), t("ag.task.caption"))
    with st.form(f"agtask-{agent.id}", border=True):
        task = st.text_area(t("ag.new.task"), value=record.get("task", ""), height=150, max_chars=builder.MAX_TASK,
                            label_visibility="collapsed")
        go = st.form_submit_button(t("ag.task.redesign"), icon=":material/auto_awesome:", type="primary")
    if go:
        _redesign(agent, settings, agents, task)
    draft = (st.session_state.get(_REDESIGN) or {}).get(agent.id)
    if draft is None:
        return
    number = st.session_state.get(f"agrd-no-{agent.id}", 0)
    with st.container(border=True, key=f"agrd-{agent.id}"):
        st.markdown(f"**{t('ag.task.result')}**")
        if draft.summary:
            st.info(draft.summary, icon=":material/smart_toy:")
        if draft.questions:
            st.warning("\n".join([f"**{t('ag.task.questions')}**", *[f"- {c.md_escape(item)}" for item in draft.questions]]),
                       icon=":material/help:")
        if set(draft.tool_groups) != set(agent.tool_groups):
            names = lambda groups: ", ".join(t(f"ag.group.{group}") for group in groups) or "—"  # noqa: E731
            st.caption(t("ag.task.tools_hint", suggested=names(draft.tool_groups), current=names(agent.tool_groups)))
        if draft.model != agent.model:
            st.caption(t("ag.task.model_hint", model=draft.model, current=agent.model))
        if draft.telegram and not record.get("telegram_connection"):
            st.caption(t("ag.task.telegram_hint"))
        prompt = st.text_area(t("ag.prompt.label"), value=draft.system_prompt, height=320,
                              max_chars=prompts.MAX_INSTRUCTIONS, key=f"agrd-prompt-{agent.id}-{number}",
                              label_visibility="collapsed")
        left, right, _rest = st.columns([1.6, 1, 3])
        accept = left.button(t("ag.task.accept"), type="primary", icon=":material/check:", key=f"agrd-ok-{agent.id}",
                             width="stretch")
        cancel = right.button(t("ag.new.cancel"), key=f"agrd-cancel-{agent.id}", width="stretch")
    if cancel:
        st.session_state[_REDESIGN].pop(agent.id, None)
        st.rerun()
    if accept:
        if not prompt.strip():
            st.error(t("ag.err.empty"))
            return
        state.save_custom_agent(settings, {**record, "task": draft.task, "summary": draft.summary or record.get("summary", ""),
                                           "routing": draft.routing or record.get("routing", ""),
                                           "missing": draft.missing})
        state.set_agent_prompt(settings, agent.id, prompt.strip(), "save")
        log.info("agent %s: prompt rewritten from a changed task (%d characters)", agent.id, len(prompt.strip()))
        st.session_state[_REDESIGN].pop(agent.id, None)
        st.session_state[_FLASH] = t("ag.task.saved")
        st.rerun()


def _connections(agent: registry.Agent, settings: Settings) -> None:
    record = agent.record or {}
    c.section(t("ag.connections"), t("ag.connections.caption"))
    st.markdown(f"**{t('ag.need.odoo')}**")
    if "odoo" not in agent.tool_groups:
        st.caption(t("ag.odoo.off"))
    else:
        current = record.get("odoo_connection")
        row = state.connection(settings, current)
        if row is None:
            st.caption(t("ag.need.odoo.later"))
        elif row["status"] != "ok":
            st.warning(row["status_text"] or t("ag.conn.error"), icon=":material/link_off:")
        choice = conn.odoo_picker(settings, f"agodoo-{agent.id}", current)
        left, right, _rest = st.columns([1.2, 1.6, 3])
        if choice is not None and choice != current and left.button(t("ag.connect"), key=f"agodoo-set-{agent.id}",
                                                                     type="primary", icon=":material/link:"):
            connection_id = conn.store(settings, choice) if isinstance(choice, conn.Pending) else choice
            state.save_custom_agent(settings, {**record, "odoo_connection": connection_id})
            st.session_state[_FLASH] = t("ag.odoo.connected")
            st.rerun()
        if row and right.button(t("ag.odoo.recheck"), key=f"agodoo-check-{agent.id}", icon=":material/sync:"):
            ok, text = conn.recheck_odoo(settings, row)
            st.session_state[_FLASH] = text if ok else None
            if not ok:
                st.error(text)
            else:
                st.rerun()
    st.markdown(f"**{t('ag.need.telegram')}**")
    row = state.connection(settings, record.get("telegram_connection"))
    if row:
        conn.telegram_panel(settings, f"agtg-{agent.id}", row)
        if st.button(t("ag.tg.disconnect"), key=f"agtg-off-{agent.id}", icon=":material/link_off:"):
            state.delete_connection(settings, row["id"])
            log.info("agent %s: telegram bot disconnected", agent.id)
            st.session_state[_FLASH] = t("ag.tg.disconnected")
            st.rerun()
    elif st.toggle(t("ag.tg.add"), key=f"agtg-on-{agent.id}"):
        pending = conn.telegram_form(settings, f"agtg-new-{agent.id}")
        if pending is not None and st.button(t("ag.connect"), key=f"agtg-set-{agent.id}", type="primary",
                                             icon=":material/link:"):
            connection_id = conn.store(settings, pending)
            state.save_custom_agent(settings, {**record, "telegram_connection": connection_id})
            st.session_state[_FLASH] = t("ag.tg.connected", username=pending.config["username"])
            st.rerun()
    if record.get("missing"):
        st.markdown(f"**{t('ag.missing')}**")
        st.markdown("\n".join(f"- **{c.md_escape(item.get('need', ''))}** — {c.md_escape(item.get('why', ''))}"
                              for item in record["missing"]))
        st.caption(t("ag.missing.caption"))


def _editor(agent: registry.Agent, settings: Settings, saved: dict | None) -> None:
    c.section(t("ag.prompt"), t("ag.prompt.caption"))
    current = saved["text"] if saved else prompts.default_instructions(agent.id, settings)
    version = saved["id"] if saved else 0
    with st.form(f"agform-{agent.id}", border=False, enter_to_submit=False):
        text = st.text_area(t("ag.prompt.label"), value=current, height=420, max_chars=prompts.MAX_INSTRUCTIONS,
                            key=f"agtext-{agent.id}-{version}", label_visibility="collapsed")
        save_col, reset_col, _rest = st.columns([1, 1.9, 3.5])
        with save_col:
            submitted = st.form_submit_button(t("ag.save"), type="primary", icon=":material/save:", width="stretch")
        with reset_col:
            reset = st.form_submit_button(t("ag.reset.custom" if agent.custom else "ag.reset"),
                                          icon=":material/restart_alt:", key=f"agreset-{agent.id}",
                                          disabled=saved is None, help=t("ag.reset.help"), width="stretch")
    if submitted:
        _save_prompt(settings, agent, text, current)
    if reset:
        _store_prompt(settings, agent, "", "reset", t("ag.reset.done"))


def _reference(agent: registry.Agent, settings: Settings, agents: dict) -> None:
    ref = prompts.reference(agent.id, settings, agents)
    with st.expander(t("ag.reference"), icon=":material/lock:"):
        if not ref:
            st.caption(t("ag.reference.none"))
            return
        st.caption(t("ag.reference.caption"))
        st.code(ref, language=None, wrap_lines=True)


def _tools(tool_list: list) -> None:
    with st.expander(t("ag.tools.title", n=len(tool_list)), icon=":material/build:"):
        if not tool_list:
            st.caption(t("ag.tools.none"))
            return
        st.markdown("\n".join(f"- `{tool.name}` — {c.md_escape(tool.description)}" for tool in tool_list))


def _history(agent: registry.Agent, settings: Settings) -> None:
    c.section(t("ag.history"))
    rows = state.agent_prompt_history(settings, agent.id, HISTORY_ROWS)
    if not rows:
        st.caption(t("ag.history.empty"))
        return
    default = prompts.default_instructions(agent.id, settings)
    for index, row in enumerate(rows):
        text = row["text"] or default
        with st.container(key=f"aghist-{row['id']}"):
            info, view, restore = st.columns([5, 1.2, 1.2], vertical_alignment="center")
            with info:
                label = t(f"ag.action.{row['action']}") if row["action"] in ("save", "reset", "restore") else row["action"]
                current = f" · **{t('ag.history.current')}**" if index == 0 else ""
                marker = f" · {t('ag.status.default')}" if not row["text"] else ""
                st.markdown(f"{_when(row, settings)} — {label}{marker} · {t('ag.chars', n=len(text))}{current}")
            with view:
                with st.popover(t("ag.history.view"), icon=":material/visibility:", width="stretch"):
                    st.code(text, language=None, wrap_lines=True)
            with restore:
                if index > 0 and st.button(t("ag.history.restore"), icon=":material/history:",
                                           key=f"agrestore-{row['id']}", width="stretch"):
                    _store_prompt(settings, agent, row["text"], "restore", t("ag.restored"))


def _delete(agent: registry.Agent, settings: Settings, agents: dict) -> None:
    with st.expander(t("ag.delete.title"), icon=":material/delete:"):
        parent = agents.get(agent.parent or registry.ROOT)
        kids = registry.children(agents, agent.id)
        st.caption(t("ag.delete.caption"))
        if kids:
            st.caption(t("ag.delete.kids", names=", ".join(kid.name for kid in kids), parent=parent.name if parent else ""))
        sure = st.checkbox(t("ag.delete.confirm", name=agent.name), key=f"agdel-ok-{agent.id}")
        if st.button(t("ag.delete"), type="primary", disabled=not sure, key=f"agdel-{agent.id}", icon=":material/delete:"):
            state.delete_custom_agent(settings, agent.id)
            log.info("agent %s deleted", agent.id)
            st.session_state[_FLASH] = t("ag.deleted", name=agent.name)
            _open(None)


def _detail(agent: registry.Agent, settings: Settings, agents: dict) -> None:
    if st.button(t("ag.back"), icon=":material/arrow_back:", key="agback"):
        _open(None)
    st.title(f":material/{agent.icon}: {c.md_escape(agent.name)}", anchor=False)
    if agent.role:
        st.caption(agent.role)
    _flash()
    saved = prompts.saved_instructions(agent.id, settings)
    tool_list = tools(agent.id, settings, agents)
    _summary(agent, settings, agents, saved, len(tool_list))
    if agent.custom:
        _try_out(agent, settings, agents)
        _custom_settings(agent, settings, agents)
        _task(agent, settings, agents)
        _connections(agent, settings)
    _editor(agent, settings, saved)
    _reference(agent, settings, agents)
    _tools(tool_list)
    _history(agent, settings)
    if agent.custom:
        _delete(agent, settings, agents)


@shell.guard
def render() -> None:
    ctx = shell.current()
    agent_id = st.query_params.get(QP_AGENT)
    if agent_id == NEW:
        agent_builder.render(ctx.settings, back=lambda: _open(None))
        return
    if agent_id:
        agents = registry.load(ctx.settings)
        agent = agents.get(agent_id)
        if agent is None:
            st.warning(t("ag.unknown"))
            if st.button(t("ag.back"), icon=":material/arrow_back:", key="agback"):
                _open(None)
            return
        _detail(agent, ctx.settings, agents)
        return
    left, right = st.columns([4, 1.2], vertical_alignment="bottom")
    with left:
        st.title(overrides.page_title(t("ag.title")), anchor=False)
    with right:
        if st.button(t("ag.add"), type="primary", icon=":material/add:", key="agadd", width="stretch"):
            _open(NEW)
    st.caption(t("ag.caption"))
    _flash()
    layout.render_page(PAGE, PageData(ctx))
