"""One request to the agent team: what every agent working on it shares (settings, API clients, the
tree, the dashboard context, the pictures), the tools an agent uses to call the created agents below
it, and how a created agent runs (its model, its instructions plus the dashboard's reference, its
tool groups and its own sub-agents)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from assistant import data_tools, llm, models, odoo_tools, prompts, registry, ui_agent
from assistant.llm import Tool, obj
from core import secrets, state
from core.settings import Settings
from ui.i18n import t

log = logging.getLogger("dashboard.assistant")

CUSTOM_STEPS = 8  # a created agent may need a few rounds of tool calls (more with the dashboard tools)
VIEW_STEPS = 10


@dataclass
class Call:
    """Shared by every agent that works on one request (one chat message or one Telegram message)."""

    settings: Settings
    pool: llm.ClientPool
    context: dict
    pictures: tuple = ()
    on_step: Callable[[str], None] | None = None
    agents: dict = field(default_factory=dict)
    used: list[str] = field(default_factory=list)  # agent ids in the order they worked
    changes: list[str] = field(default_factory=list)
    usage: llm.Usage = field(default_factory=llm.Usage)
    answers: dict[str, str] = field(default_factory=dict)  # tool name -> the called agent's answer
    tool_calls: list[str] = field(default_factory=list)  # tools the created agents called, in order

    def __post_init__(self) -> None:
        if not self.agents:
            self.agents = registry.load(self.settings)

    def settings_of(self, agent_id: str) -> Settings:
        return registry.settings_of(self.agents, agent_id, self.settings)

    def client_of(self, agent_id: str):
        return self.pool.get(self.settings_of(agent_id))

    def step(self, label: str) -> None:
        if self.on_step is not None:
            self.on_step(label)


def child_tools(call: Call, parent_id: str) -> list[Tool]:
    """One tool per created agent directly below ``parent_id``."""
    tools = []
    for child in registry.children(call.agents, parent_id):
        if not child.custom:
            continue

        def ask(args: dict, child=child) -> dict:
            request = str(args.get("request") or "").strip()
            if not request:
                return {"error": "empty request"}
            text = run_custom(call, child, request)
            call.answers[child.tool_name] = text
            return {"answer": text}

        routing = str((child.record or {}).get("routing") or child.role)
        tools.append(Tool(child.tool_name, f"Ask «{child.name}»: {routing}"[:1000],
                          obj({"request": {"type": "string",
                                           "description": "A complete, self-contained request with the context needed"}}),
                          ask))
    return tools


def _odoo(settings: Settings, connection_id: str | None) -> odoo_tools.Odoo | None:
    row = state.connection(settings, connection_id)
    if not row or row["status"] != "ok" or not row["secret"]:
        return None
    config = row["config"]
    try:
        key = secrets.unprotect(row["secret"])
    except secrets.SecretsUnavailable:
        log.warning("odoo connection %s: the key cannot be decrypted", connection_id)
        return None
    return odoo_tools.Odoo(config.get("url", ""), config.get("db", ""), config.get("login", ""), key)


def group_tools(call: Call, agent: registry.Agent, changes: list[str]) -> list[Tool]:
    """The tools of an agent's tool groups (a name used by two groups -- undo -- is kept once)."""
    settings, context = call.settings, call.context
    tools: list[Tool] = []
    if "data" in agent.tool_groups:
        tools += data_tools.build(settings, context)
    if "odoo" in agent.tool_groups:
        odoo = _odoo(settings, (agent.record or {}).get("odoo_connection"))
        if odoo is not None:
            tools += odoo_tools.build(odoo, settings.agents.odoo_models)
    if "view" in agent.tool_groups:
        tools += ui_agent.tools(settings, context, changes)
    unique: dict[str, Tool] = {}
    for tool in tools:
        unique.setdefault(tool.name, tool)
    return list(unique.values())


def run_custom(call: Call, agent: registry.Agent, message: str, history: list[dict] | None = None) -> str:
    """Runs a created agent on one request; its usage and dashboard changes are added to the call."""
    call.step(t("chat.step.custom", name=agent.name))
    call.used.append(agent.id)
    changes: list[str] = []
    settings = call.settings_of(agent.id)
    tools = group_tools(call, agent, changes) + child_tools(call, agent.id)
    messages = [{"role": item["role"], "content": item["text"]} for item in history or [] if item.get("text")]
    while messages and messages[0]["role"] != "user":  # a conversation starts with the user
        messages.pop(0)
    messages.append(llm.user_message(message, call.pictures))
    steps = max(settings.chat.max_steps, VIEW_STEPS if "view" in agent.tool_groups else CUSTOM_STEPS)
    result = llm.run_agent(call.pool.get(settings), settings, name=f"custom:{agent.id}",
                           system=prompts.system(agent.id, call.settings, call.agents), messages=messages,
                           context=prompts.custom_context(call.context, agent, call.settings), tools=tools,
                           max_steps=steps)
    call.usage.add(result.usage)
    call.changes.extend(changes)
    call.tool_calls.extend(result.tool_calls)
    return result.text or t("chat.err.internal")


def model_label(agent: registry.Agent) -> str:
    return models.label(agent.model)
