"""«Agent qo'shish»: the user names the agent and describes its task (or starts from an example); the
designer (assistant/builder.py) proposes the model, the place in the tree, the tool groups, the
connections it needs, what is not available, questions when the task is unclear and the system prompt.
The user answers the questions right there (the answers go into the task and it is analysed again),
changes anything, adds the connections it asks for and creates the agent. The new agent's page opens
with «Sinab ko'rish», where it can be tried at once (views/agents.py)."""

from __future__ import annotations

import logging

import streamlit as st

from assistant import builder, models, prompts, registry
from assistant.llm import AgentError
from core import state
from core.settings import Settings
from ui import components as c
from ui.i18n import t
from views import agent_connections as conn

log = logging.getLogger("dashboard.agents")

DRAFT, DRAFT_NO = "ag_draft", "ag_draft_no"
REANALYZE = "ag_reanalyze"  # set by «Javoblar bilan qayta tahlil qilish»: analyse the task again
NAME, TASK = "agnew-name", "agnew-task"
MIN_TASK = 40
# Examples that fill the form (texts in ui/i18n.py: ag.tpl.<key> and ag.tpl.<key>.task)
TEMPLATES = (("complaints", "report"), ("speed", "timer"), ("odoo", "shopping_cart"), ("telegram", "send"))


def _use_template(key: str) -> None:
    """on_click of an example: fills the name and the task (a proposal for another task is dropped)."""
    st.session_state[NAME] = t(f"ag.tpl.{key}")
    st.session_state[TASK] = t(f"ag.tpl.{key}.task")
    st.session_state.pop(DRAFT, None)


def _answer(key: str, questions: list[str]) -> None:
    """on_click of «Javoblar bilan qayta tahlil qilish»: the answers are added to the task text and the
    task is analysed again in the same run (render)."""
    lines = []
    for index, question in enumerate(questions):
        answer = " ".join(str(st.session_state.get(f"{key}-answer-{index}") or "").split())
        if answer:
            lines.append(f"- {question} — {answer}")
    if not lines:
        st.session_state[f"{key}-noanswer"] = True
        return
    task = str(st.session_state.get(TASK) or "").rstrip()
    addition = "\n\n" + t("ag.new.answers.heading") + "\n" + "\n".join(lines)
    st.session_state[TASK] = (task + addition)[: builder.MAX_TASK]
    st.session_state[REANALYZE] = True


def _analyze(settings: Settings, agents: dict, name: str, task: str) -> None:
    name, task = name.strip(), task.strip()
    if len(name) < 2:
        st.warning(t("ag.new.err.name"))
        return
    if len(task) < MIN_TASK:
        st.warning(t("ag.new.err.task", n=MIN_TASK))
        return
    if any(agent.custom and agent.name.casefold() == name.casefold() for agent in agents.values()):
        st.warning(t("ag.new.err.exists", name=name))
        return
    with st.spinner(t("ag.new.working")):
        try:
            draft = builder.design(settings, name, task, agents)
        except AgentError as exc:
            st.error(str(exc))
            return
        except Exception:  # noqa: BLE001 -- the page must not break
            log.exception("agent designer failed")
            st.error(t("chat.err.internal"))
            return
    log.info("agent designed: model=%s groups=%s telegram=%s tokens=%d/%d", draft.model, ",".join(draft.tool_groups),
             draft.telegram, draft.usage.input_tokens, draft.usage.output_tokens)
    st.session_state[DRAFT] = draft
    st.session_state[DRAFT_NO] = st.session_state.get(DRAFT_NO, 0) + 1  # fresh widgets for the new proposal
    st.rerun()


def _create(settings: Settings, draft: builder.Draft, choice: dict) -> None:
    if not choice["prompt"].strip():
        st.error(t("ag.err.empty"))
        return
    if choice["telegram_on"] and not isinstance(choice["telegram"], conn.Pending):
        st.error(t("ag.new.err.telegram"))
        return
    agent_id = registry.new_id()
    odoo = choice["odoo"]
    odoo_id = conn.store(settings, odoo) if isinstance(odoo, conn.Pending) else odoo
    telegram_id = conn.store(settings, choice["telegram"]) if choice["telegram_on"] else None
    state.save_custom_agent(settings, {
        "id": agent_id, "name": draft.name, "summary": draft.summary, "routing": choice["routing"].strip(),
        "task": draft.task, "parent": choice["parent"], "model": choice["model"], "tool_groups": choice["groups"],
        "odoo_connection": odoo_id if "odoo" in choice["groups"] else None, "telegram_connection": telegram_id,
        "generated_prompt": choice["prompt"].strip(), "missing": draft.missing,
    })
    log.info("agent %s created (model=%s, parent=%s, groups=%s, telegram=%s)", agent_id, choice["model"],
             choice["parent"], ",".join(choice["groups"]), bool(telegram_id))
    for key in (DRAFT, NAME, TASK):
        st.session_state.pop(key, None)
    st.session_state["ag_flash"] = t("ag.new.created", name=draft.name)
    st.query_params["a"] = agent_id
    st.rerun()


def _review(settings: Settings, agents: dict, draft: builder.Draft) -> None:
    number = st.session_state.get(DRAFT_NO, 0)
    key = f"agd{number}"
    st.divider()
    c.section(t("ag.new.result"))
    if draft.summary:
        st.info(draft.summary, icon=":material/smart_toy:")
    if draft.questions:
        with st.container(border=True, key=f"{key}-questions"):
            st.markdown(f":material/help: **{t('ag.new.questions')}**")
            for index, question in enumerate(draft.questions):
                st.text_input(c.md_escape(question), key=f"{key}-answer-{index}", max_chars=300)
            st.button(t("ag.new.answer"), icon=":material/auto_awesome:", key=f"{key}-answer-go", on_click=_answer,
                      args=(key, list(draft.questions)))
            if st.session_state.pop(f"{key}-noanswer", False):
                st.warning(t("ag.new.answers.none"))

    model_ids = [model.id for model in models.available(settings)]
    left, right = st.columns(2)
    with left:
        model = st.selectbox(t("ag.model"), model_ids, format_func=models.label, key=f"{key}-model",
                             index=model_ids.index(draft.model) if draft.model in model_ids else 0)
        st.caption(draft.model_reason)
        if not models.has_key(settings, "anthropic"):
            st.caption(t("ag.model.claude_hint"))
    with right:
        parents = registry.parent_options(agents)
        parent = st.selectbox(t("ag.parent.pick"), parents, format_func=lambda agent_id: agents[agent_id].name,
                              index=parents.index(draft.parent) if draft.parent in parents else 0, key=f"{key}-parent")
        st.caption(draft.parent_reason)

    st.markdown(f"**{t('ag.tools.pick')}**")
    groups = []
    columns = st.columns(len(registry.TOOL_GROUPS))
    for column, group in zip(columns, registry.TOOL_GROUPS.values()):
        with column:
            if st.checkbox(t(f"ag.group.{group.id}"), value=group.id in draft.tool_groups, key=f"{key}-group-{group.id}",
                           help=t(f"ag.group.{group.id}.help")):
                groups.append(group.id)
    if draft.tools_reason:
        st.caption(draft.tools_reason)
    if any(registry.TOOL_GROUPS[group].writes for group in groups):
        st.caption(t("ag.tools.writes"))

    c.section(t("ag.needs"), t("ag.needs.caption"))
    odoo = None
    if "odoo" in groups:
        st.markdown(f"**{t('ag.need.odoo')}**")
        odoo = conn.odoo_picker(settings, f"{key}-odoo")
        if odoo is None:
            st.caption(t("ag.need.odoo.later"))
    telegram_on = st.toggle(t("ag.need.telegram"), value=draft.telegram, key=f"{key}-tg-on")
    if draft.telegram_reason:
        st.caption(draft.telegram_reason)
    telegram = conn.telegram_form(settings, f"{key}-tg") if telegram_on else None
    if draft.missing:
        st.markdown(f"**{t('ag.missing')}**")
        st.markdown("\n".join(f"- **{c.md_escape(item['need'])}** — {c.md_escape(item['why'])}" for item in draft.missing))
        st.caption(t("ag.missing.caption"))
    elif "odoo" not in groups and not telegram_on:
        st.caption(t("ag.needs.none"))

    c.section(t("ag.prompt"), t("ag.new.prompt.caption"))
    prompt = st.text_area(t("ag.prompt.label"), value=draft.system_prompt, height=380,
                          max_chars=prompts.MAX_INSTRUCTIONS, key=f"{key}-prompt", label_visibility="collapsed")
    with st.expander(t("ag.routing"), icon=":material/alt_route:"):
        routing = st.text_area(t("ag.routing.label"), value=draft.routing, height=80, key=f"{key}-routing")

    left, right, _rest = st.columns([1.4, 1, 3])
    if left.button(t("ag.new.create"), type="primary", icon=":material/check:", key=f"{key}-create", width="stretch"):
        _create(settings, draft, {"model": model, "parent": parent, "groups": groups, "odoo": odoo,
                                  "telegram_on": telegram_on, "telegram": telegram, "prompt": prompt,
                                  "routing": routing or draft.routing})
    if right.button(t("ag.new.cancel"), key=f"{key}-cancel", width="stretch"):
        st.session_state.pop(DRAFT, None)
        st.rerun()


def render(settings: Settings, back) -> None:
    """The «Agent qo'shish» view. ``back`` returns to the tree."""
    if st.button(t("ag.back"), icon=":material/arrow_back:", key="agback"):
        st.session_state.pop(DRAFT, None)
        back()
    st.title(t("ag.new.title"), anchor=False)
    st.caption(t("ag.new.caption"))
    agents = registry.load(settings)
    if sum(1 for agent in agents.values() if agent.custom) >= settings.agents.max_agents:
        st.warning(t("ag.new.limit", n=settings.agents.max_agents))
        return
    if not settings.chat.ready:
        st.warning(t("ag.new.no_ai"))
        return
    draft = st.session_state.get(DRAFT)
    if draft is None:
        st.caption(t("ag.tpl.caption"))
        for column, (key, icon) in zip(st.columns(len(TEMPLATES)), TEMPLATES):
            column.button(t(f"ag.tpl.{key}"), icon=f":material/{icon}:", key=f"agtpl-{key}", on_click=_use_template,
                          args=(key,), width="stretch")
    with st.form("agnew-form", border=True):
        name = st.text_input(t("ag.new.name"), max_chars=builder.MAX_NAME, key=NAME,
                             placeholder=t("ag.new.name.placeholder"))
        task = st.text_area(t("ag.new.task"), height=220, max_chars=builder.MAX_TASK, key=TASK,
                            placeholder=t("ag.new.task.placeholder"))
        go = st.form_submit_button(t("ag.new.reanalyze") if draft else t("ag.new.analyze"), type="primary",
                                   icon=":material/auto_awesome:")
    if go or st.session_state.pop(REANALYZE, False):
        _analyze(settings, agents, name, task)
    if draft is not None:
        _review(settings, agents, draft)
