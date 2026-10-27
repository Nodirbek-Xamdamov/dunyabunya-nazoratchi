"""All agents of the AI assistant as one tree: the three built-in agents and the agents created on the
«AI agentlar» page (core.state.custom_agents). An agent calls the agents below it through tools.
At most three levels: Boshqaruvchi -> agent -> sub-agent (each level adds model calls, so time and cost).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from assistant import models
from core import state
from core.settings import Settings
from ui.i18n import t

ROOT = "orchestrator"
BUILTIN = ("orchestrator", "data", "view")
ICONS = {"orchestrator": "hub", "data": "query_stats", "view": "dashboard_customize"}
CUSTOM_ICON = "smart_toy"
MAX_DEPTH = 3


@dataclass(frozen=True)
class ToolGroup:
    id: str
    writes: bool  # changes the dashboard for everyone
    connection: str | None  # a connection it needs (odoo)
    about: str  # English, for the agent designer


TOOL_GROUPS = {
    "data": ToolGroup("data", False, None,
                      "Read the customer-communication monitoring database (the company's Telegram customer groups): "
                      "key numbers, groups ranked by risk, message categories, unanswered requests, requests waiting "
                      "now, complaints, message search, staff answer speed, daily trends, the bot's daily report "
                      "summaries."),
    "odoo": ToolGroup("odoo", False, "odoo",
                      "Read the company's Odoo (ERP/CRM), read-only: customers, sales orders and their lines, "
                      "invoices, CRM leads, products and stock, purchases, tasks, users. Needs an Odoo connection "
                      "the user adds on the page."),
    "view": ToolGroup("view", True, None,
                      "Change what the dashboard shows for everyone: hide, show, move and rename sections and pages; "
                      "build new sections and pages from the dashboard's data."),
}


@dataclass(frozen=True)
class Agent:
    id: str
    name: str
    role: str
    icon: str
    parent: str | None
    custom: bool
    model: str  # the model it runs with
    model_chosen: bool  # chosen on the page (False = the default from dashboard\.env)
    tool_groups: tuple[str, ...] = ()
    record: dict | None = None  # the custom_agents row

    @property
    def tool_name(self) -> str:
        return "agent_" + self.id.replace("-", "_")


def new_id() -> str:
    return f"ag-{uuid.uuid4().hex[:8]}"


def load(settings: Settings) -> dict[str, Agent]:
    """Built-in agents first, then the created ones (oldest first)."""
    agents: dict[str, Agent] = {}
    for agent_id in BUILTIN:
        chosen = state.agent_model(settings, agent_id)
        ok = models.usable(settings, chosen)
        agents[agent_id] = Agent(agent_id, t(f"ag.{agent_id}"), t(f"ag.{agent_id}.role"), ICONS[agent_id],
                                 None if agent_id == ROOT else ROOT, False,
                                 chosen if ok else settings.chat.model, bool(ok))
    for row in state.custom_agents(settings):
        ok = models.usable(settings, row["model"])
        agents[row["id"]] = Agent(row["id"], row["name"], row["summary"], CUSTOM_ICON, row["parent"], True,
                                  row["model"] if ok else settings.chat.model, True,
                                  tuple(group for group in row["tool_groups"] if group in TOOL_GROUPS), row)
    for agent_id, agent in list(agents.items()):  # a lost parent (deleted in another tab) or a too deep branch
        if agent.custom and (agent.parent not in agents or depth(agents, agent_id) > MAX_DEPTH):
            agents[agent_id] = replace(agent, parent=ROOT)
    return agents


def children(agents: dict[str, Agent], agent_id: str) -> list[Agent]:
    return [agent for agent in agents.values() if agent.parent == agent_id]


def depth(agents: dict[str, Agent], agent_id: str) -> int:
    """Boshqaruvchi = 1. A broken chain (a cycle) counts as too deep."""
    level, seen, current = 1, set(), agents.get(agent_id)
    while current is not None and current.parent:
        if current.id in seen:
            return MAX_DEPTH + 1
        seen.add(current.id)
        level += 1
        current = agents.get(current.parent)
    return level


def descendants(agents: dict[str, Agent], agent_id: str) -> set[str]:
    found, todo = set(), [agent_id]
    while todo:
        for child in children(agents, todo.pop()):
            if child.id not in found:
                found.add(child.id)
                todo.append(child.id)
    return found


def height(agents: dict[str, Agent], agent_id: str | None) -> int:
    """Levels of the branch that starts at the agent (a new agent = 1)."""
    if agent_id is None:
        return 1
    below = [height(agents, child.id) for child in children(agents, agent_id)]
    return 1 + (max(below) if below else 0)


def parent_options(agents: dict[str, Agent], agent_id: str | None = None) -> list[str]:
    """Where an agent (None = a new one) may sit: not under itself or its own sub-agents, and the
    tree stays within MAX_DEPTH levels."""
    blocked = {agent_id} | descendants(agents, agent_id) if agent_id else set()
    branch = height(agents, agent_id)
    return [candidate for candidate in agents if candidate not in blocked
            and depth(agents, candidate) + branch <= MAX_DEPTH]


def model_of(agents: dict[str, Agent], agent_id: str, settings: Settings) -> str:
    agent = agents.get(agent_id)
    return agent.model if agent else settings.chat.model


def settings_of(agents: dict[str, Agent], agent_id: str, settings: Settings) -> Settings:
    return models.for_model(settings, model_of(agents, agent_id, settings))
